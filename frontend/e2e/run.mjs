// E2E runner: starts every server it needs on its own ports, gives each suite group a fresh,
// seeded database, runs the suites (each covers mock mode and API mode) and prints a summary.
//
//   node e2e/run.mjs                 # development stack: Vite (mock + API mode) + Flask + SQLite
//   node e2e/run.mjs --prod          # production simulation: API build + static site that rewrites
//                                    #   /api/* to the backend (same origin, like render.yaml)
//                                    #   + FLASK_ENV=staging + PostgreSQL (PGlite); adds the deploy suite
//   node e2e/run.mjs labs timeline   # only groups containing these suites
//
// Never touches backend/instance/app.db or servers already running on the usual dev ports.
// Every server binds 127.0.0.1 explicitly (Vite's "localhost" would otherwise bind IPv6 [::1]
// only on some systems). Before each suite, every server must still respond.
// Needs: npm install (frontend), the Python venv (../venv or $PYTHON), Chrome (or $CHROME_PATH).
import { spawn } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'

import { E2E_DIR, FRONTEND_DIR, OUT } from './lib/env.mjs'

const args = process.argv.slice(2)
const PROD = args.includes('--prod')
const filters = args.filter((a) => !a.startsWith('--'))
const HOST = '127.0.0.1'
const PORT = { mock: 5273, web: 5274, api: 5100, pg: 55433 }
const URL = {
  mock: `http://${HOST}:${PORT.mock}`,
  web: `http://${HOST}:${PORT.web}`,
  api: `http://${HOST}:${PORT.api}/api/v1`,
}
const BACKEND_DIR = path.resolve(FRONTEND_DIR, '..', 'backend')
const PYTHON = process.env.PYTHON ?? path.resolve(FRONTEND_DIR, '..', 'venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const VITE = path.join(FRONTEND_DIR, 'node_modules/vite/bin/vite.js')
const PGLITE = path.join(FRONTEND_DIR, 'node_modules/@electric-sql/pglite-socket/dist/scripts/server.js')
// Outside the Vite root: a build written inside it makes the mock-mode dev server reload pages.
const PROD_DIST = path.join(os.tmpdir(), 'cancer-care-e2e-dist')
const LOGS = path.join(OUT, 'logs')
fs.mkdirSync(LOGS, { recursive: true })

// Groups run in order; suites inside a group share one database (later suites build on earlier ones).
const GROUPS = [
  { suites: ['mock-clock'], backend: false },
  { suites: ['auth'] },
  { suites: ['symptom', 'review'] },
  { suites: ['vitals', 'risk'] },
  { suites: ['notifications'] },
  { suites: ['labs'] },
  { suites: ['timeline'] },
  { suites: ['mobile'] },
  { suites: ['config'] },
  { suites: ['patients'] },
  { suites: ['chemotherapy'] },
  { suites: ['appointments'] },
  { suites: ['assessments'] },
  { suites: ['corrections'] },
  { suites: ['portal'] },
  { suites: ['admin'] },
  { suites: ['reminders'] },
  { suites: ['sessions'] },
  // 1-minute access tokens (the existing JWT_ACCESS_TOKEN_MINUTES setting): real expiry → refresh
  { suites: ['refresh'], env: { JWT_ACCESS_TOKEN_MINUTES: '1' } },
  ...(PROD ? [{ suites: ['deploy'] }] : []),
].filter((g) => !filters.length || g.suites.some((s) => filters.some((f) => s.includes(f))))

// ------------------------------------------------------------------ process helpers
const children = new Set()
const crashed = [] // servers that exited while they were still needed
function start(name, cmd, cmdArgs, { cwd = FRONTEND_DIR, env = {}, oneShot = false } = {}) {
  const log = fs.openSync(path.join(LOGS, `${name}.log`), 'w')
  const child = spawn(cmd, cmdArgs, { cwd, env: { ...process.env, ...env }, stdio: ['ignore', log, log], windowsHide: true })
  child.label = name
  child.stopping = false
  children.add(child)
  child.on('exit', (code) => {
    children.delete(child)
    if (!oneShot && !child.stopping) crashed.push(`${name} (pid ${child.pid}) exited with code ${code} — see logs/${name}.log`)
  })
  return child
}
function stop(child) {
  if (!child || child.exitCode !== null) return Promise.resolve()
  child.stopping = true
  return new Promise((resolve) => {
    child.once('exit', resolve)
    // Windows: the venv python.exe is a launcher that starts the real interpreter as a child
    // process, so kill the whole tree; elsewhere a signal to the process is enough.
    if (process.platform === 'win32') spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true })
    else child.kill()
  })
}
function run(name, cmd, cmdArgs, opts) {
  return new Promise((resolve) => {
    const child = start(name, cmd, cmdArgs, { ...opts, oneShot: true })
    child.on('exit', (code) => resolve(code))
  })
}
async function waitFor(check, what, ms = 60000) {
  const until = Date.now() + ms
  while (Date.now() < until) {
    if (await check().catch(() => false)) return
    if (crashed.length) throw new Error(`while waiting for ${what}: ${crashed.join('; ')}`)
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error(`timed out after ${ms / 1000}s waiting for ${what}`)
}
const httpOk = (url) => () => fetch(url, { signal: AbortSignal.timeout(5000) }).then((r) => r.ok)
const portOpen = (port) => () => new Promise((resolve) => {
  const s = net.connect(port, HOST, () => { s.end(); resolve(true) })
  s.on('error', () => resolve(false))
})
async function ensureFree(port) {
  // a server stopped just before may still be releasing its port
  const until = Date.now() + 15000
  while (await portOpen(port)()) {
    if (Date.now() > until) throw new Error(`port ${port} is already in use — stop that process first`)
    await new Promise((r) => setTimeout(r, 250))
  }
}
const ready = [] // startup table
async function started(child, url, check) {
  const t0 = Date.now()
  await waitFor(check, `${child.label} at ${url}`)
  ready.push({ server: child.label, url, pid: child.pid, ms: Date.now() - t0 })
  console.log(`  ready  ${child.label.padEnd(22)} ${url.padEnd(34)} pid ${child.pid}  (${Date.now() - t0} ms)`)
}
process.on('SIGINT', async () => { await Promise.all([...children].map(stop)); process.exit(130) })

// ------------------------------------------------------------------ stack
const DB_FILE = path.join(OUT, 'e2e.db')
const backendEnv = () => (PROD
  ? {
      FLASK_ENV: 'staging',
      DATABASE_URL: `postgresql://postgres:postgres@${HOST}:${PORT.pg}/postgres`,
      SECRET_KEY: crypto.randomBytes(32).toString('hex'),
      JWT_SECRET_KEY: crypto.randomBytes(32).toString('hex'),
      CORS_ORIGINS: URL.web,
      SEED_DEMO_PASSWORD: 'Demo@1234',
      FLASK_DEBUG: '',
      PYTHONIOENCODING: 'utf-8',
    }
  : { FLASK_ENV: 'development', DATABASE_URL: `sqlite:///${DB_FILE.replaceAll('\\', '/')}`, PYTHONIOENCODING: 'utf-8' })

async function startFrontends() {
  await ensureFree(PORT.mock)
  await ensureFree(PORT.web)
  const mock = start('vite-mock', process.execPath, [VITE, '--host', HOST, '--port', String(PORT.mock), '--strictPort'], { env: { VITE_USE_MOCK: 'true' } })
  let web
  if (PROD) {
    const code = await run('build-prod', process.execPath, [VITE, 'build', '--outDir', PROD_DIST, '--emptyOutDir'], {
      env: { VITE_USE_MOCK: 'false', VITE_API_TIMEOUT_MS: '60000' }, // same-origin /api/v1 (no API URL in the bundle)
    })
    if (code !== 0) throw new Error('production build failed (see logs/build-prod.log)')
    web = start('static-site', process.execPath, [path.join(E2E_DIR, 'lib/static-server.mjs'), PROD_DIST, String(PORT.web), `http://${HOST}:${PORT.api}`])
  } else {
    web = start('vite-api', process.execPath, [VITE, '--host', HOST, '--port', String(PORT.web), '--strictPort'], {
      env: { VITE_USE_MOCK: 'false', VITE_DEV_API_TARGET: `http://${HOST}:${PORT.api}` },
    })
  }
  await started(mock, `${URL.mock}/`, httpOk(`${URL.mock}/`))
  await started(web, `${URL.web}/`, httpOk(`${URL.web}/`))
  return [mock, web]
}

async function startBackend(label, extraEnv = {}) {
  await ensureFree(PORT.api)
  let pg = null
  if (PROD) {
    await ensureFree(PORT.pg)
    pg = start(`pglite-${label}`, process.execPath, [PGLITE, '--db=memory://', `--port=${PORT.pg}`, `--host=${HOST}`])
    await started(pg, `postgresql://${HOST}:${PORT.pg}`, portOpen(PORT.pg))
  } else {
    for (const f of [DB_FILE, `${DB_FILE}-journal`]) fs.rmSync(f, { force: true })
  }
  const env = { ...backendEnv(), ...extraEnv }
  for (const step of [['db', 'upgrade'], ['seed', 'dev']]) {
    const code = await run(`backend-${step[0]}-${label}`, PYTHON, ['-m', 'flask', '--app', 'run', ...step], { cwd: BACKEND_DIR, env })
    if (code !== 0) throw new Error(`flask ${step.join(' ')} failed (see logs/backend-${step[0]}-${label}.log)`)
  }
  // PGlite accepts one connection at a time: serve requests one by one in the simulation.
  const serverArgs = ['-m', 'flask', '--app', 'run', 'run', '--host', HOST, '--port', String(PORT.api), ...(PROD ? ['--without-threads'] : [])]
  const api = start(`backend-${label}`, PYTHON, serverArgs, { cwd: BACKEND_DIR, env })
  await started(api, `${URL.api}/health`, async () => {
    const r = await fetch(`${URL.api}/health`, { signal: AbortSignal.timeout(5000) })
    return r.ok && (await r.json()).database === 'ok'
  })
  return [api, pg]
}

/** Every server must still answer before a suite starts. */
async function assertAlive(withBackend) {
  if (crashed.length) throw new Error(crashed.join('; '))
  const probes = [['mock-mode frontend', `${URL.mock}/`], ['API-mode frontend', `${URL.web}/`]]
  if (withBackend) probes.push(['backend health', `${URL.api}/health`])
  for (const [what, url] of probes) {
    if (!(await httpOk(url)().catch(() => false))) throw new Error(`${what} not responding at ${url} before the suite`)
  }
}

function runSuite(suite) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [path.join(E2E_DIR, `${suite}.e2e.mjs`)], {
      cwd: FRONTEND_DIR,
      env: { ...process.env, E2E_MOCK_URL: URL.mock, E2E_WEB_URL: URL.web, E2E_API_URL: URL.api, E2E_ARTIFACTS: OUT },
      windowsHide: true,
    })
    let out = ''
    child.stdout.on('data', (d) => { out += d })
    child.stderr.on('data', (d) => { out += d })
    child.on('exit', (code) => {
      fs.writeFileSync(path.join(LOGS, `suite-${suite}.log`), out)
      const m = out.match(/(\d+)\/(\d+) checks passed/)
      const lines = out.split('\n')
      const tally = (re) => ({
        pass: lines.filter((l) => l.startsWith('[PASS]') && re.test(l)).length,
        fail: lines.filter((l) => l.startsWith('[FAIL]') && re.test(l)).length,
      })
      resolve({
        suite, code, out, summary: m ? m[0] : 'no summary', passed: code === 0 && m && m[1] === m[2],
        mock: tally(/^\[\w+\] mock\b/i), api: tally(/^\[\w+\] api\b/i),
      })
    })
  })
}

// ------------------------------------------------------------------ main
const results = []
try {
  console.log(`E2E ${PROD ? 'production simulation (API build + staging + PostgreSQL)' : 'development stack (Vite + Flask + SQLite)'}`)
  await startFrontends()
  for (const [i, group] of GROUPS.entries()) {
    const label = group.suites.join('+')
    const withBackend = group.backend !== false
    const [api, pg] = withBackend ? await startBackend(`${i}-${label}`, group.env) : [null, null]
    for (const suite of group.suites) {
      await assertAlive(withBackend)
      const r = await runSuite(suite)
      results.push(r)
      const modes = `MOCK ${r.mock.pass}/${r.mock.pass + r.mock.fail}  API ${r.api.pass}/${r.api.pass + r.api.fail}`
      console.log(`${r.passed ? 'PASS' : 'FAIL'}  ${suite.padEnd(14)} ${r.summary.padEnd(22)} ${modes}`)
      if (!r.passed) console.log(r.out.split('\n').filter((l) => l.startsWith('[FAIL]') || /Error/.test(l)).slice(0, 12).join('\n'))
    }
    await stop(api)
    await stop(pg)
  }
} catch (err) {
  console.error(`\nE2E runner error: ${err.message}`)
  results.push({ suite: 'runner', passed: false, mock: { pass: 0, fail: 0 }, api: { pass: 0, fail: 0 } })
} finally {
  await Promise.all([...children].map(stop))
}
const sum = (k, f) => results.reduce((n, r) => n + (r[k]?.[f] ?? 0), 0)
const total = results.reduce((n, r) => n + Number(r.summary?.match(/^(\d+)/)?.[1] ?? 0), 0)
const failed = results.filter((r) => !r.passed)
console.log(`\n${results.length - failed.length}/${results.length} suites passed, ${total} checks`
  + ` (MOCK-labelled ${sum('mock', 'pass')}/${sum('mock', 'pass') + sum('mock', 'fail')},`
  + ` API-labelled ${sum('api', 'pass')}/${sum('api', 'pass') + sum('api', 'fail')}) — logs in ${path.relative(FRONTEND_DIR, LOGS)}`)
process.exit(failed.length ? 1 : 0)
