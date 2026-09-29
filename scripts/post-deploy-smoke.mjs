#!/usr/bin/env node
// Post-deploy smoke test (Render staging / demo). Verifies from the outside, like a browser does,
// through the frontend origin only (same-origin /api proxy):
//   frontend over HTTPS (+ SPA rewrite, assets, security headers) · /api/v1/health through the
//   proxy (GET) · login through the proxy (POST) · the refresh-token Set-Cookie and its attributes ·
//   the access token · Authorization forwarded · refresh with the cookie (Cookie forwarded,
//   rotation, same session) · the API after refresh · optional: a real access-token expiry → 401
//   TOKEN_EXPIRED → refresh → the API again · logout (POST) · after logout the refresh token and
//   the access token are refused.
//
// Usage (Node 20+, no dependencies; run from anywhere):
//   SMOKE_PASSWORD=<SEED_DEMO_PASSWORD> node scripts/post-deploy-smoke.mjs --web https://cancer-care-web.onrender.com
//     [--api https://cancer-care-api.onrender.com]   also check the API service directly / that the bundle does not name it
//     [--email patient01@demo.local]                  demo account to sign in with (default patient01)
//     [--wait-expiry]                                 wait for a real access-token expiry (default 15 min) and check the refresh path
//     [--allow-http]                                  local runs only (e.g. the E2E static server); Render must be HTTPS
//
// Never prints the password, an access token, a refresh token or a whole cookie — only check names,
// status codes, error codes and cookie attribute names. Exit code 0 = every check passed.

const args = process.argv.slice(2)
const opt = (name, fallback = null) => {
  const i = args.indexOf(`--${name}`)
  return i >= 0 && args[i + 1] && !args[i + 1].startsWith('--') ? args[i + 1] : fallback
}
const flag = (name) => args.includes(`--${name}`)
const WEB = (opt('web') ?? process.env.SMOKE_WEB_URL ?? '').replace(/\/+$/, '')
const API = (opt('api') ?? process.env.SMOKE_API_URL ?? '').replace(/\/+$/, '')
const EMAIL = opt('email') ?? process.env.SMOKE_EMAIL ?? 'patient01@demo.local'
const PASSWORD = process.env.SMOKE_PASSWORD
const WAIT_EXPIRY = flag('wait-expiry')
const ALLOW_HTTP = flag('allow-http')
const TIMEOUT_MS = 90000 // a Free web service needs about a minute to wake up

if (!WEB || !PASSWORD) {
  console.error('usage: SMOKE_PASSWORD=<demo password> node scripts/post-deploy-smoke.mjs --web https://<frontend-host> [--api https://<api-host>] [--email <demo account>] [--wait-expiry]')
  process.exit(2)
}

// ------------------------------------------------------------------ reporting (no secrets)
const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok: !!ok })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`)
  return !!ok
}
function skip(name, why) {
  console.log(`SKIP  ${name}  (${why})`)
}
const section = (title) => console.log(`\n== ${title}`)

// ------------------------------------------------------------------ HTTP helpers
const XHR = { 'X-Requested-With': 'XMLHttpRequest' }
let jar = null // the refresh_token cookie value, as a browser would keep it (never printed)

async function request(path, { method = 'GET', token = null, cookie = false, headers = {}, body, base = WEB } = {}) {
  const h = { Accept: 'application/json', ...headers }
  if (token) h.Authorization = `Bearer ${token}`
  if (cookie && jar) h.Cookie = `refresh_token=${jar}`
  if (body !== undefined) h['Content-Type'] = 'application/json'
  const res = await fetch(base + path, {
    method, headers: h, body: body === undefined ? undefined : JSON.stringify(body), redirect: 'manual', signal: AbortSignal.timeout(TIMEOUT_MS),
  })
  const text = await res.text()
  let json = null
  try {
    json = JSON.parse(text)
  } catch {
    // not JSON (HTML, empty 204)
  }
  return { res, status: res.status, text, json, setCookies: res.headers.getSetCookie?.() ?? [] }
}

/** The refresh_token Set-Cookie of a response → { value, attrs } (attrs: lower-case name → value). */
function refreshCookie(setCookies) {
  const raw = setCookies.find((c) => c.startsWith('refresh_token='))
  if (!raw) return null
  const [pair, ...rest] = raw.split(';')
  const attrs = Object.fromEntries(rest.map((p) => {
    const [k, ...v] = p.trim().split('=')
    return [k.toLowerCase(), v.join('=')]
  }))
  return { value: pair.slice('refresh_token='.length), attrs }
}
const cleared = (c) => c && (c.value === '' || c.attrs['max-age'] === '0' || (c.attrs.expires && Date.parse(c.attrs.expires) < Date.now()))
/** JWT payload claims (only read locally to compare sessions / expiry; the token itself is never printed). */
const claims = (jwt) => {
  try {
    return JSON.parse(Buffer.from(jwt.split('.')[1], 'base64url').toString())
  } catch {
    return {}
  }
}
const errorCode = (r) => r.json?.error?.code ?? '-'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function waitForHealth(base, label) {
  const until = Date.now() + 3 * 60000
  let last = null
  while (Date.now() < until) {
    try {
      last = await request('/api/v1/health', { base })
      if (last.status === 200) return last
    } catch (e) {
      last = { status: 0, error: e.name }
    }
    console.log(`      waiting for ${label} to wake up (status ${last.status})…`)
    await sleep(10000)
  }
  return last
}

// ------------------------------------------------------------------ checks
try {
  section('Frontend')
  check('frontend URL uses HTTPS', WEB.startsWith('https://') || ALLOW_HTTP, ALLOW_HTTP && !WEB.startsWith('https://') ? 'http allowed for a local run' : '')
  const home = await request('/')
  check('GET / → 200 HTML', home.status === 200 && /text\/html/.test(home.res.headers.get('content-type') ?? ''), `status ${home.status}`)
  check('security headers: X-Content-Type-Options nosniff, X-Frame-Options DENY',
    home.res.headers.get('x-content-type-options') === 'nosniff' && home.res.headers.get('x-frame-options') === 'DENY')
  const deep = await request('/patient/timeline')
  check('SPA rewrite: GET /patient/timeline → the app (index.html), not 404', deep.status === 200 && deep.text === home.text, `status ${deep.status}`)
  const asset = home.text.match(/src="(\/assets\/[^"]+\.js)"/)?.[1]
  if (asset) {
    const js = await request(asset)
    check('GET /assets/*.js → JavaScript file', js.status === 200 && /javascript/.test(js.res.headers.get('content-type') ?? ''), `status ${js.status}`)
    check('bundle: same-origin /api/v1, no mock data', js.text.includes('/api/v1') && !js.text.includes('mock-user'))
    if (API) check('bundle does not name the API host (the browser never calls it)', !js.text.includes(new URL(API).host))
  } else {
    check('index.html references a /assets/*.js bundle', false)
  }

  section('/api proxy: health (GET)')
  const health = await waitForHealth(WEB, 'the API behind the proxy')
  check('GET <frontend>/api/v1/health → 200 {status: ok, database: ok}', health?.status === 200 && health.json?.status === 'ok' && health.json?.database === 'ok', `status ${health?.status}`)
  check('the proxied response is JSON from Flask (not the SPA index.html)', /application\/json/.test(health?.res?.headers.get('content-type') ?? ''))
  if (API) {
    const direct = await request('/api/v1/health', { base: API })
    check('GET <api>/api/v1/health → 200 (API service itself)', direct.status === 200, `status ${direct.status}`)
  }

  section('Login (POST through the proxy) and the refresh-token cookie')
  const login = await request('/api/v1/auth/login', { method: 'POST', body: { email: EMAIL, password: PASSWORD } })
  const data = login.json?.data ?? {}
  if (!check(`POST /api/v1/auth/login (${EMAIL}) → 200`, login.status === 200, `status ${login.status}, ${errorCode(login)}`)) throw new Error('login failed — later checks need a session')
  let access = data.access_token
  check('access token: a JWT with sub / sid / exp, token_type Bearer, expires_in', typeof access === 'string' && access.split('.').length === 3
    && claims(access).sid && claims(access).exp && data.token_type === 'Bearer' && data.expires_in > 0, `expires_in ${data.expires_in}s`)
  check('the response body has no refresh token', !('refresh_token' in data) && !/refresh_token/i.test(login.text))
  const c1 = refreshCookie(login.setCookies)
  check('Set-Cookie: refresh_token reached the browser through the proxy', !!c1)
  if (c1) {
    const a = c1.attrs
    check('cookie attributes: HttpOnly, Secure, SameSite=Strict, Path=/api/v1/auth, no Domain, future Expires',
      'httponly' in a && 'secure' in a && a.samesite?.toLowerCase() === 'strict' && a.path === '/api/v1/auth' && !('domain' in a) && Date.parse(a.expires) > Date.now(),
      `attributes: ${Object.keys(a).join(', ')}`)
    check('the cookie value does not appear in the response body', !login.text.includes(c1.value))
    jar = c1.value
  }
  const sid = claims(access).sid

  section('Authorization forwarded')
  const me = await request('/api/v1/auth/me', { token: access })
  check('GET /api/v1/auth/me with Bearer → 200, the signed-in account', me.status === 200 && me.json?.data?.email === EMAIL, `status ${me.status}`)
  const anon = await request('/api/v1/auth/me')
  check('without Authorization → 401 UNAUTHENTICATED', anon.status === 401 && errorCode(anon) === 'UNAUTHENTICATED', `status ${anon.status}`)

  section('Refresh (Cookie forwarded, rotation)')
  const noXhr = await request('/api/v1/auth/refresh', { method: 'POST', cookie: true })
  check('POST /auth/refresh without X-Requested-With → 403 (CSRF defence)', noXhr.status === 403, `status ${noXhr.status}`)
  const r1 = await request('/api/v1/auth/refresh', { method: 'POST', cookie: true, headers: XHR })
  const c2 = refreshCookie(r1.setCookies)
  check('POST /auth/refresh with the cookie → 200 new access token, same session', r1.status === 200 && r1.json?.data?.access_token !== access
    && claims(r1.json?.data?.access_token ?? '').sid === sid, `status ${r1.status}, ${errorCode(r1)}`)
  check('refresh rotated the cookie (new value, same attributes)', c2 && c2.value !== jar && 'httponly' in c2.attrs && c2.attrs.path === '/api/v1/auth')
  check('the refresh body has no refresh token', !/refresh_token/i.test(r1.text))
  if (r1.status === 200) access = r1.json.data.access_token
  if (c2) jar = c2.value
  const after = await request('/api/v1/auth/me', { token: access })
  check('API request with the refreshed access token → 200', after.status === 200, `status ${after.status}`)

  section('Access-token expiry → refresh → API')
  if (WAIT_EXPIRY) {
    const waitMs = claims(access).exp * 1000 - Date.now() + 5000
    console.log(`      waiting ${Math.round(waitMs / 1000)} s for the access token to expire…`)
    await sleep(Math.max(0, waitMs))
    const expired = await request('/api/v1/auth/me', { token: access })
    check('the expired access token → 401 TOKEN_EXPIRED', expired.status === 401 && errorCode(expired) === 'TOKEN_EXPIRED', `status ${expired.status}, ${errorCode(expired)}`)
    const r2 = await request('/api/v1/auth/refresh', { method: 'POST', cookie: true, headers: XHR })
    check('refresh after expiry → 200 (the session is still valid)', r2.status === 200, `status ${r2.status}, ${errorCode(r2)}`)
    if (r2.status === 200) access = r2.json.data.access_token
    const c3 = refreshCookie(r2.setCookies)
    if (c3) jar = c3.value
    const again = await request('/api/v1/auth/me', { token: access })
    check('the API request succeeds again after refresh', again.status === 200, `status ${again.status}`)
  } else {
    skip('real access-token expiry', 'run with --wait-expiry to wait for it (default lifetime 15 min)')
  }

  section('Logout (POST with Authorization + Cookie)')
  const lastAccess = access
  const out = await request('/api/v1/auth/logout', { method: 'POST', token: access, cookie: true })
  check('POST /api/v1/auth/logout → 204', out.status === 204, `status ${out.status}`)
  check('logout clears the refresh cookie (Set-Cookie through the proxy)', cleared(refreshCookie(out.setCookies)))
  const dead = await request('/api/v1/auth/refresh', { method: 'POST', cookie: true, headers: XHR })
  check('after logout the refresh token cannot refresh → 401 REFRESH_TOKEN_REVOKED', dead.status === 401 && errorCode(dead) === 'REFRESH_TOKEN_REVOKED', `status ${dead.status}, ${errorCode(dead)}`)
  check('the refused refresh clears the cookie', cleared(refreshCookie(dead.setCookies)))
  const deadAccess = await request('/api/v1/auth/me', { token: lastAccess })
  check('after logout the access token is refused → 401', deadAccess.status === 401, `status ${deadAccess.status}`)
} catch (e) {
  // never echo request data: only the kind of failure
  check('smoke test ran to the end', false, e.name === 'TimeoutError' ? 'timed out waiting for the service' : e.message)
}

const failed = results.filter((r) => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} checks passed${failed.length ? ` — failed: ${failed.map((r) => r.name).join('; ')}` : ''}`)
process.exit(failed.length ? 1 : 0)
