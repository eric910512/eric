// 皮膚照片 (Phase 1b), in mock mode and API mode. With photos on (development stack):
// patient (390 px): consent (versioned draft 待院方審核) before any picker → HEIC / unreadable file refused with a
// reason → API: a failed upload is retried with the same Idempotency-Key → preview, status per photo, 3 at most,
// remove / choose again, body site → send with the report → thumbnails, larger view with the answers →
// photo history: body-site filter, two time points side by side → a text-only report still works → the daily
// report is untouched. API: no public URL, image only with a token, admin 403, nurse 200 with no-store / nosniff,
// no EXIF in the bytes, forged MIME type 415. nurse: thumbnails on the review card → larger view (date, answers)
// → photo history + comparison → 390 px. Mock / API response structure.
// Production simulation (staging, no object storage): photos safely off — the patient is told, text reports
// still work, the upload API answers 503.
import fs from 'node:fs'
import path from 'node:path'
import zlib from 'node:zlib'

import puppeteer from 'puppeteer-core'
import { API_URL, CHROME, CHROME_ARGS, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}
const idle = (p) => p.waitForNetworkIdle({ idleTime: 400, timeout: 8000 }).catch(() => {})
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })
const errors = []
const desk = { width: 1280, height: 1000, deviceScaleFactor: 1 }
const phone = { width: 390, height: 844, deviceScaleFactor: 1 }
const MOCK_P1 = '72ba2de4-f19d-4daf-96f9-03a5e83c07d0'

// ---------------------------------------------------------------- synthetic fixtures (no real photos)
const FIX = path.join(OUT, 'photo-fixtures')
fs.mkdirSync(FIX, { recursive: true })
function pngBytes(w, h, [r, g, b]) {
  const row = Buffer.alloc(1 + w * 3)
  const raw = Buffer.alloc((1 + w * 3) * h)
  for (let y = 0; y < h; y++) {
    row[0] = 0
    for (let x = 0; x < w; x++) row.set([(r + x) & 255, (g + y) & 255, b], 1 + x * 3)
    row.copy(raw, y * row.length)
  }
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length)
    const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(Buffer.concat([Buffer.from(type), data])) >>> 0)
    return Buffer.concat([len, Buffer.from(type), data, crc])
  }
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr.set([8, 2, 0, 0, 0], 8)
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))])
}
const fixture = (name, data) => { const f = path.join(FIX, name); fs.writeFileSync(f, data); return f }
const IMG = Object.fromEntries([['a', [200, 60, 60]], ['b', [60, 60, 200]], ['c', [60, 200, 60]], ['d', [220, 200, 40]], ['e', [120, 40, 160]], ['f', [40, 160, 160]],
  ['r1', [90, 30, 30]], ['r2', [30, 90, 30]], ['r3', [30, 30, 90]]]
  .map(([k, color]) => [k, fixture(`skin-${k}.png`, pngBytes(640, 480, color))]))
const HEIC = fixture('IMG_0007.heic', Buffer.concat([Buffer.from([0, 0, 0, 0x18]), Buffer.from('ftypheic'), Buffer.alloc(4), Buffer.from('mif1heic'), Buffer.alloc(200)]))
const BAD = fixture('broken.jpg', Buffer.from('this is not an image'.repeat(20)))

const api = async (p, token, opts = {}) => {
  const r = await fetch(API_URL + p, { ...opts, headers: { ...(opts.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  const buf = Buffer.from(await r.arrayBuffer())
  let body = null
  try { body = JSON.parse(buf.toString('utf8')) } catch { /* image */ }
  return { status: r.status, headers: r.headers, buf, body }
}
const tokenFor = async (email) => (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password: 'Demo@1234' }) })).body.data.access_token
const text = (p, sel = 'body') => p.$eval(sel, (e) => e.innerText).catch(() => '')
const waitSel = (p, sel, timeout = 10000) => p.waitForSelector(sel, { timeout }).then(() => true, () => false)
const waitFn = (p, fn, arg, timeout = 15000) => p.waitForFunction(fn, { timeout }, arg).then(() => true, () => false)
const noOverflow = (p) => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const count = (p, sel) => p.$$eval(sel, (els) => els.length)
const setValue = (p, selector, value) => p.$eval(selector, (el, value) => {
  el.value = value
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
}, value)
const click = (p, sel) => p.$eval(sel, (b) => b.click())
const go = async (p, to) => {
  await p.evaluate((to) => { history.pushState({}, '', to); dispatchEvent(new PopStateEvent('popstate')) }, to)
  await idle(p)
}
async function signIn(p, email) {
  await go(p, '/login')
  await p.waitForSelector('input[name=email]')
  await setValue(p, 'input[name=email]', email)
  await setValue(p, 'input[name=password]', 'Demo@1234')
  await p.click('button[type=submit]')
  await idle(p)
}
const signOut = async (p) => {
  await p.evaluate(() => [...document.querySelectorAll('button, a')].find((b) => b.innerText.trim() === '登出')?.click())
  await waitFn(p, () => location.pathname.startsWith('/login'))
}
const choose = async (p, ...files) => (await p.$('[data-photo-input-library]')).uploadFile(...files)
const imagesLoaded = (p, sel, n) => waitFn(p, ([sel, n]) => {
  const imgs = [...document.querySelectorAll(sel)]
  return imgs.length >= n && imgs.every((i) => i.complete && i.naturalWidth > 0)
}, [sel, n])
const answer = (p, code, value) => p.$eval(`[data-question=${code}] input[value="${value}"]`, (r) => r.click())
function shape(v) {
  if (Array.isArray(v)) return v.length ? [shape(v[0])] : []
  if (v && typeof v === 'object') return Object.fromEntries(Object.keys(v).sort().map((k) => [k, shape(v[k])]))
  return v === null ? null : typeof v
}
function shapeDiff(a, b, at = '') {
  if (a === null || b === null) return []
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) return [`${at}: array vs ${typeof b}`]
    return a.length && b.length ? shapeDiff(a[0], b[0], `${at}[]`) : []
  }
  if (typeof a === 'object' && typeof b === 'object') {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)])
    return [...keys].flatMap((k) => (!(k in a) ? [`${at}.${k}: mock only`] : !(k in b) ? [`${at}.${k}: API only`] : shapeDiff(a[k], b[k], `${at}.${k}`)))
  }
  return a === b ? [] : [`${at}: ${a} vs ${b}`]
}
const shapes = { MOCK: {}, API: {} }

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource|net::ERR_FAILED/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(phone)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })
  let pid = MOCK_P1
  let pt, nt, at
  let enabled = true
  if (mode === 'API') {
    pt = await tokenFor('patient01@demo.local')
    nt = await tokenFor('nurse01@demo.local')
    at = await tokenFor('admin01@demo.local')
    pid = (await api('/patients?q=P00001', nt)).body.data[0].id
    enabled = (await api('/symptom-photos/settings', pt)).body.data.upload_enabled
  }
  const mockCall = (name, args) => p.evaluate(async (a) => {
    try {
      return { status: 200, body: await (await import('/src/mock/api.js'))[a.name](...a.args) }
    } catch (e) {
      return { status: e.status ?? 500, body: { error: { code: e.code, message: e.message } } }
    }
  }, { name, args })

  await signIn(p, 'patient01@demo.local')
  await go(p, '/patient/skin')
  check(`${mode} patient: photo section on the skin self-assessment`, await waitSel(p, '[data-photo-section]') && await waitSel(p, '[data-skin-form] [data-question]'))

  if (!enabled) {
    // ---------------------------------------------------------------- production simulation: photos safely off
    check(`${mode} photos off (no object storage): the patient is told, no picker, no consent form`, await waitSel(p, '[data-photo-disabled]')
      && (await text(p, '[data-photo-disabled]')).includes('照片上傳目前未開放') && (await count(p, '[data-photo-picker], [data-consent-accept]')) === 0)
    await answer(p, 'skin_itch', 'yes')
    await click(p, '[data-skin-submit]')
    check(`${mode} photos off: a text-only report still works`, await waitSel(p, '[data-skin-done]'))
    const fd = new FormData()
    fd.append('file', new Blob([fs.readFileSync(IMG.a)], { type: 'image/png' }), 'a.png')
    const up = await api('/patients/me/symptom-photos', pt, { method: 'POST', body: fd, headers: { 'Idempotency-Key': crypto.randomUUID() } })
    check(`${mode} photos off: upload API → 503 PHOTO_UPLOADS_DISABLED (no fallback storage)`, up.status === 503 && up.body?.error?.code === 'PHOTO_UPLOADS_DISABLED', JSON.stringify(up.body))
    const st = (await api('/symptom-photos/settings', pt)).body.data
    check(`${mode} photos off: settings report why (no local disk in staging)`, st.upload_enabled === false && st.view_enabled === false && !!st.reason, st.reason)
    await signOut(p)
    await ctx.close()
    continue
  }

  // ---------------------------------------------------------------- consent first
  check(`${mode} consent shown before any picker, marked 草稿・待院方審核`, await waitSel(p, '[data-photo-consent] [data-consent-draft]')
    && (await count(p, '[data-photo-picker]')) === 0)
  const consentText = await text(p, '[data-photo-consent]')
  check(`${mode} consent: purpose, who may view, not real-time, upload ≠ reviewed, 119`, ['照片用途', '目前負責照護您的醫療人員', '不會立即有人查看',
    '不代表醫護人員已經看過', '119', '不具法律效力'].every((w) => consentText.includes(w)))
  await click(p, '[data-consent-accept]')
  check(`${mode} accepted → camera / library buttons`, await waitSel(p, '[data-photo-picker] [data-photo-camera]') && await waitSel(p, '[data-photo-library]'))
  check(`${mode} the picker accepts JPEG / PNG / WebP (+ HEIC to explain it), camera uses capture`,
    (await p.$eval('[data-photo-input-library]', (i) => i.accept)).includes('image/webp') && (await p.$eval('[data-photo-input-camera]', (i) => i.getAttribute('capture'))) === 'environment')

  // ---------------------------------------------------------------- refused files: a reason each
  await choose(p, HEIC)
  check(`${mode} HEIC → refused with a clear reason (最相容)`, await waitFn(p, () => document.querySelector('[data-photo-item=invalid] [data-photo-error]')?.innerText.includes('HEIC')))
  await click(p, '[data-photo-item=invalid] [data-photo-remove]')
  await choose(p, BAD)
  check(`${mode} unreadable file → refused (無法讀取)`, await waitFn(p, () => document.querySelector('[data-photo-item=invalid] [data-photo-error]')?.innerText.includes('無法讀取')))
  await click(p, '[data-photo-item=invalid] [data-photo-remove]')
  await waitFn(p, () => !document.querySelector('[data-photo-item]'))

  // ---------------------------------------------------------------- retry safety (API; frontend/src/utils/photoUploadRetry.js)
  // Two real uploads at most here (the API allows 8 per patient per 10 minutes and the flow below needs 6).
  if (mode === 'API') {
    const posts = [] // Idempotency-Key of every upload POST the page sends
    const fake = [] // answers for the next POSTs, instead of the API (the request never reaches it)
    await p.setRequestInterception(true)
    const onRequest = (r) => {
      if (r.isInterceptResolutionHandled()) return
      if (r.method() === 'POST' && r.url().endsWith('/symptom-photos')) {
        posts.push(r.headers()['idempotency-key'])
        const f = fake.shift()
        if (f) return r.respond({ status: f.status, contentType: 'application/json', body: JSON.stringify({ error: { code: f.code, message: f.message, details: [] } }) })
      }
      r.continue()
    }
    p.on('request', onRequest)
    const itemText = () => text(p, '[data-photo-item]')

    // 400 / 403 from the API: the photo is refused, not "try again later"
    for (const [status, code, message] of [[403, 'PHOTO_CONSENT_REQUIRED', '請先閱讀並同意照片使用說明'], [400, 'VALIDATION_ERROR', '請求內容有誤']]) {
      fake.push({ status, code, message })
      await choose(p, IMG.r1)
      check(`${mode} retry safety: ${status} ${code} → 無法使用 with the reason, no 重試 (not treated as a temporary failure)`,
        await waitSel(p, '[data-photo-item=invalid]') && (await itemText()).includes(message) && !(await p.$('[data-photo-item] [data-photo-retry]'))
        && !!(await p.$('[data-photo-item] [data-photo-remove]')))
      await click(p, '[data-photo-item] [data-photo-remove]')
      await waitFn(p, () => !document.querySelector('[data-photo-item]'))
    }

    // 503 PHOTO_STORAGE_UNAVAILABLE (the API stored nothing) → the retry uses a NEW key and succeeds; a rapid
    // double click on 重試 sends ONE request
    posts.length = 0
    fake.push({ status: 503, code: 'PHOTO_STORAGE_UNAVAILABLE', message: '照片暫時無法儲存，請稍後再試' })
    await choose(p, IMG.r2)
    check(`${mode} retry safety: 503 PHOTO_STORAGE_UNAVAILABLE → 上傳失敗 with the server's reason + 重試`,
      await waitSel(p, '[data-photo-item=failed] [data-photo-retry]') && (await itemText()).includes('照片暫時無法儲存'))
    await p.$eval('[data-photo-retry]', (b) => { b.click(); b.click(); b.click() }) // three clicks in the same moment
    check(`${mode} retry safety: 503 → retry succeeds (已上傳)`, await waitSel(p, '[data-photo-item=uploaded]'))
    await idle(p)
    check(`${mode} retry safety: the retry after a 503 used a NEW Idempotency-Key`, posts.length >= 2 && posts[0] !== posts[1], JSON.stringify(posts))
    check(`${mode} retry safety: three rapid clicks on 重試 → exactly one upload request`, posts.length === 2, `${posts.length} POSTs`)
    await click(p, '[data-photo-item] [data-photo-remove]')
    await waitFn(p, () => !document.querySelector('[data-photo-item]'))
    p.off('request', onRequest)
    await p.setRequestInterception(false)

    // the answer is lost AFTER the API created the photo (response stage) → the result is unknown → the retry
    // keeps the key → the API answers with the SAME photo: no duplicate
    const cdp = await p.createCDPSession()
    const seen = [] // { key, status, id } of each upload POST, as the API answered it
    cdp.on('Fetch.requestPaused', async (ev) => {
      if (ev.request.method !== 'POST') return cdp.send('Fetch.continueRequest', { requestId: ev.requestId }).catch(() => {})
      const { body, base64Encoded } = await cdp.send('Fetch.getResponseBody', { requestId: ev.requestId })
      const json = JSON.parse(base64Encoded ? Buffer.from(body, 'base64').toString('utf8') : body)
      seen.push({ key: ev.request.headers['Idempotency-Key'] ?? ev.request.headers['idempotency-key'], status: ev.responseStatusCode, id: json?.data?.id })
      if (seen.length === 1) return cdp.send('Fetch.failRequest', { requestId: ev.requestId, errorReason: 'TimedOut' }).catch(() => {})
      cdp.send('Fetch.continueResponse', { requestId: ev.requestId }).catch(() => {})
    })
    await cdp.send('Fetch.enable', { patterns: [{ urlPattern: '*/symptom-photos', requestStage: 'Response' }] })
    await choose(p, IMG.r3)
    check(`${mode} retry safety: answer lost after the API created the photo → 上傳失敗, "無法確認" + 重試`,
      await waitSel(p, '[data-photo-item=failed] [data-photo-retry]') && (await itemText()).includes('無法確認照片是否已上傳'), seen.map((s) => s.status).join(','))
    await p.$eval('[data-photo-retry]', (b) => { b.click(); b.click() })
    check(`${mode} retry safety: unknown result → retry → 已上傳`, await waitSel(p, '[data-photo-item=uploaded]'))
    await idle(p)
    check(`${mode} retry safety: the retry kept the Idempotency-Key and the API answered with the photo it had already created (no duplicate)`,
      seen.length === 2 && seen[0].key && seen[0].key === seen[1].key && seen[0].status === 201 && seen[1].id === seen[0].id, JSON.stringify(seen))
    await cdp.send('Fetch.disable')
    await cdp.detach()
    await click(p, '[data-photo-item] [data-photo-remove]')
    await waitFn(p, () => !document.querySelector('[data-photo-item]'))
    await idle(p)
  }

  // ---------------------------------------------------------------- upload (API: first try fails on the network)
  const keys = []
  if (mode === 'API') {
    await p.setRequestInterception(true)
    let failNext = true
    p.on('request', (r) => {
      if (r.isInterceptResolutionHandled()) return
      if (r.method() === 'POST' && r.url().endsWith('/symptom-photos')) {
        keys.push(r.headers()['idempotency-key'])
        if (failNext) { failNext = false; return r.abort('failed') }
      }
      r.continue()
    })
  }
  await answer(p, 'skin_itch', 'yes')
  await setValue(p, '[data-skin-notes]', '右側頸部，照射後第三週')
  await choose(p, IMG.a)
  if (mode === 'API') {
    check(`${mode} network failure → 上傳失敗 + reason + 重試; answers kept`, await waitSel(p, '[data-photo-item=failed] [data-photo-retry]')
      && (await text(p, '[data-photo-item=failed]')).includes('網路不穩定') && (await p.$eval('[data-skin-notes]', (t) => t.value)).includes('照射後第三週'))
    await click(p, '[data-skin-submit]')
    check(`${mode} sending with a failed photo is blocked, nothing lost`, await waitSel(p, '[data-photo-blocked]') && !(await p.$('[data-skin-done]')))
    await click(p, '[data-photo-retry]')
  }
  check(`${mode} preview + 已上傳`, await waitSel(p, '[data-photo-item=uploaded] [data-photo-preview]') && await imagesLoaded(p, '[data-photo-preview]', 1))
  if (mode === 'API') check(`${mode} the retry used the same Idempotency-Key`, keys.length === 2 && keys[0] === keys[1], JSON.stringify(keys))
  await choose(p, IMG.b, IMG.c, IMG.d)
  check(`${mode} at most 3: two more added, the third refused with a notice`, await waitFn(p, () => document.querySelectorAll('[data-photo-item=uploaded]').length === 3)
    && (await text(p, '[data-photo-notice]')).includes('最多 3 張'))
  await p.$$eval('[data-photo-item=uploaded]', (li) => li[2].querySelector('[data-photo-remove]').click())
  check(`${mode} remove → 2 left`, await waitFn(p, () => document.querySelectorAll('[data-photo-item]').length === 2))
  await choose(p, IMG.d)
  check(`${mode} choose again → 3`, await waitFn(p, () => document.querySelectorAll('[data-photo-item=uploaded]').length === 3))
  const sites = ['head_neck', 'head_neck', 'axilla_left']
  for (let i = 0; i < 3; i++) await p.$$eval('[data-photo-site]', (s, [i, v]) => { s[i].value = v; s[i].dispatchEvent(new Event('change', { bubbles: true })) }, [i, sites[i]])
  check(`${mode} 390 px: form with photos, no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/skin-photo-${mode}-picker.png`, fullPage: true })
  await click(p, '[data-skin-submit]')
  check(`${mode} report sent with its photos`, await waitSel(p, '[data-skin-done]'))
  check(`${mode} history: the report shows 3 thumbnails (loaded with the user's token)`, await imagesLoaded(p, '[data-skin-record-photos] img', 3))
  await p.$eval('[data-skin-record-photos] button', (b) => b.click())
  check(`${mode} larger view: full image, upload time, the patient's answers`, await waitSel(p, '[data-skin-viewer]') && await imagesLoaded(p, '[data-skin-viewer] img', 1)
    && (await text(p, '[data-skin-viewer]')).includes('上傳時間') && (await text(p, '[data-skin-viewer-answers]')).includes('搔癢：有'))
  await p.keyboard.press('Escape')
  check(`${mode} Esc closes the larger view`, await waitFn(p, () => !document.querySelector('[data-skin-viewer]')))

  // a second report (another time point) with one photo, then a text-only one
  await click(p, '[data-skin-again]')
  await answer(p, 'rt_dermatitis_redness', 'dark')
  await choose(p, IMG.e)
  await waitSel(p, '[data-photo-item=uploaded]')
  await p.$eval('[data-photo-site]', (s) => { s.value = 'head_neck'; s.dispatchEvent(new Event('change', { bubbles: true })) })
  await click(p, '[data-skin-submit]')
  await waitSel(p, '[data-skin-done]')
  await click(p, '[data-skin-again]')
  await answer(p, 'skin_itch', 'no')
  await click(p, '[data-skin-submit]')
  check(`${mode} a text-only report still works`, await waitSel(p, '[data-skin-done]'))

  // ---------------------------------------------------------------- photo history + comparison
  check(`${mode} 我的皮膚照片: 4 sent photos`, await waitFn(p, () => document.querySelectorAll('[data-skin-photo-history] [data-skin-photo-thumb]').length === 4)
    && await imagesLoaded(p, '[data-skin-photo-history] [data-skin-photo-thumb] img', 4))
  await p.select('[data-skin-site-filter]', 'head_neck')
  check(`${mode} filter by body site (頭頸部) → 3`, await waitFn(p, () => document.querySelectorAll('[data-skin-photo-history] [data-skin-photo-thumb]').length === 3))
  await click(p, '[data-skin-compare-toggle]')
  check(`${mode} side by side: two time points, each with its photos`, await waitSel(p, '[data-skin-compare]')
    && await imagesLoaded(p, '[data-compare-side=left] img', 2) && await imagesLoaded(p, '[data-compare-side=right] img', 1))
  const sides = await p.$$eval('[data-compare-side] select', (s) => s.map((x) => ({ value: x.value, label: x.selectedOptions[0].innerText, first: x.selectedIndex })))
  check(`${mode} the two time points differ (oldest vs newest, numbered)`, sides.length === 2 && sides[0].value !== sides[1].value
    && sides[0].label.startsWith('1.') && sides[1].label.startsWith('2.'), JSON.stringify(sides))
  check(`${mode} comparison is one body site only (頭頸部): every compared photo is from it`, (await text(p, '[data-compare-note]')).includes('只比較同一部位（頭頸部）')
    && (await p.$$eval('[data-compare-photo-site]', (b) => b.map((x) => x.dataset.comparePhotoSite))).every((s) => s === 'head_neck'))
  check(`${mode} comparison says it does not judge better / worse`, (await text(p, '[data-compare-note]')).includes('不會判斷改善或惡化')
    && (await text(p, '[data-skin-compare]')).match(/改善了|惡化了|好轉/) === null)
  await p.select('[data-compare-site]', 'axilla_left')
  check(`${mode} another site with a single time point → "還不能比較", never mixed with 頭頸部`, await waitSel(p, '[data-compare-single]')
    && (await count(p, '[data-compare-photo-site]')) === 0)
  check(`${mode} 390 px: history / comparison, no horizontal overflow`, await noOverflow(p))
  await p.select('[data-compare-site]', 'head_neck')
  await p.screenshot({ path: `${OUT}/skin-photo-${mode}-history.png`, fullPage: true })
  await click(p, '[data-skin-compare-toggle]')

  // a photo without a body site: 「位置未指定」, its own group (never compared with a named site)
  await click(p, '[data-skin-again]')
  await answer(p, 'skin_itch', 'yes')
  await choose(p, IMG.f)
  await waitSel(p, '[data-photo-item=uploaded]')
  await click(p, '[data-skin-submit]')
  await waitSel(p, '[data-skin-done]')
  await p.select('[data-skin-site-filter]', '') // the list was filtered to 頭頸部 above
  check(`${mode} a photo without a body site is labelled 位置未指定`, await waitFn(p, () =>
    document.querySelectorAll('[data-skin-photo-history] [data-skin-photo-thumb]').length === 5
    && document.querySelector('[data-photo-site-label=unspecified]')?.innerText === '位置未指定'))
  check(`${mode} the site filter offers 位置未指定 as its own choice`, (await p.$$eval('[data-skin-site-filter] option', (o) => o.map((x) => x.value))).includes('unspecified'))
  check(`${mode} same-day photos keep their own time under each thumbnail`, (await p.$$eval('[data-skin-photo-history] li p', (ps) => ps.every((x) => /\d{2}:\d{2}・/.test(x.innerText)))))

  await go(p, '/patient/symptoms')
  check(`${mode} daily report untouched (not reported today)`, await waitSel(p, '[data-daily-report][data-state=idle]'))

  // ---------------------------------------------------------------- API-level checks
  // the patient is signed in here: compare the patient's view on both sides (nurses also get a follow_up summary, Phase 1c)
  const list = mode === 'API' ? (await api('/symptoms/records/me?form_code=rt_skin_self_assessment', pt)).body
    : (await mockCall('mockListRecords', [pid, { formCode: 'rt_skin_self_assessment' }])).body
  const withPhotos = list.data.find((r) => r.photos.length === 3)
  shapes[mode].record = shape(withPhotos)
  const hist = mode === 'API' ? (await api(`/patients/${pid}/symptom-photos`, nt)).body : (await mockCall('mockPatientPhotos', [pid, {}])).body
  shapes[mode].photo = shape(hist.data[0])
  check(`${mode} report carries 3 photos with body sites (metadata only)`, !!withPhotos && withPhotos.photos.map((x) => x.body_site).sort().join() === 'axilla_left,head_neck,head_neck'
    && !JSON.stringify(withPhotos).includes('storage_key'))
  if (mode === 'API') {
    const photoId = withPhotos.photos[0].id
    const anon = await api(`/symptom-photos/${photoId}/content`)
    const admin = await api(`/symptom-photos/${photoId}/content`, at)
    const nurse = await api(`/symptom-photos/${photoId}/content?variant=full`, nt)
    check(`${mode} no token → 401; admin → 403`, anon.status === 401 && admin.status === 403)
    check(`${mode} assigned nurse → 200 image/jpeg, no-store, nosniff`, nurse.status === 200 && nurse.headers.get('content-type') === 'image/jpeg'
      && nurse.headers.get('cache-control').startsWith('private, no-store') && nurse.headers.get('x-content-type-options') === 'nosniff')
    check(`${mode} stored image has no EXIF segment`, !nurse.buf.includes(Buffer.from('Exif')) && nurse.buf[0] === 0xff && nurse.buf[1] === 0xd8)
    const page = await fetch(`${WEB_URL}/photos/${photoId}/full.jpg`)
    check(`${mode} no public URL: a guessed path returns the app page, never an image`, !(page.headers.get('content-type') ?? '').startsWith('image/'))
    const fd = new FormData()
    fd.append('file', new Blob([fs.readFileSync(IMG.f)], { type: 'image/jpeg' }), 'forged.jpg') // PNG bytes declared as JPEG
    const forged = await api('/patients/me/symptom-photos', pt, { method: 'POST', body: fd, headers: { 'Idempotency-Key': crypto.randomUUID() } })
    check(`${mode} forged MIME type (PNG declared as JPEG) → 415 PHOTO_TYPE_MISMATCH`, forged.status === 415 && forged.body?.error?.code === 'PHOTO_TYPE_MISMATCH')
    const del = await api(`/symptom-photos/${photoId}`, pt, { method: 'DELETE' })
    check(`${mode} a sent photo cannot be deleted by the patient → 409 PHOTO_RETAINED`, del.status === 409 && del.body?.error?.code === 'PHOTO_RETAINED')
  }
  await signOut(p)

  // ---------------------------------------------------------------- nurse
  await p.setViewport(desk)
  await signIn(p, 'nurse01@demo.local')
  await go(p, `/nurse/${pid}`)
  check(`${mode} nurse: review card shows the 3 thumbnails`, await waitSel(p, '[data-record-photos]', 15000)
    && await waitFn(p, () => [...document.querySelectorAll('[data-record-photos]')].some((ul) => ul.querySelectorAll('img').length === 3
      && [...ul.querySelectorAll('img')].every((i) => i.complete && i.naturalWidth > 0))))
  await p.$$eval('[data-record-photos]', (uls) => uls.find((ul) => ul.querySelectorAll('button').length === 3).querySelector('button').click())
  check(`${mode} nurse: larger view with the upload date and the patient's answers / notes`, await waitSel(p, '[data-skin-viewer]')
    && await imagesLoaded(p, '[data-skin-viewer] img', 1) && (await text(p, '[data-skin-viewer]')).includes('上傳時間')
    && (await text(p, '[data-skin-viewer-answers]')).includes('病人填寫的症狀') && (await text(p, '[data-skin-viewer-answers]')).includes('照射後第三週'))
  await p.keyboard.press('Escape')
  await waitFn(p, () => !document.querySelector('[data-skin-viewer]'))
  check(`${mode} nurse: 皮膚照片紀錄 with the 5 photos (one 位置未指定)`, await waitFn(p, () => document.querySelectorAll('[data-skin-photo-history] [data-skin-photo-thumb]').length === 5
    && !!document.querySelector('[data-skin-photo-history] [data-photo-site-label=unspecified]')))
  await click(p, '[data-skin-compare-toggle]')
  check(`${mode} nurse: side-by-side comparison of one site (頭頸部, the only site with two time points)`, await waitSel(p, '[data-skin-compare]')
    && await imagesLoaded(p, '[data-skin-compare] img', 2) && (await p.$eval('[data-compare-site]', (s) => s.value)) === 'head_neck'
    && (await p.$$eval('[data-compare-photo-site]', (b) => b.map((x) => x.dataset.comparePhotoSite))).every((s) => s === 'head_neck'))
  await p.screenshot({ path: `${OUT}/skin-photo-${mode}-nurse.png`, fullPage: true })
  await p.setViewport(phone)
  await go(p, `/nurse/${pid}`)
  check(`${mode} nurse 390 px: photos shown, no horizontal overflow`, await waitSel(p, '[data-record-photos]', 15000) && await noOverflow(p))
  await signOut(p)
  await ctx.close()
}

for (const k of ['record', 'photo']) {
  if (!shapes.API[k]) continue // production simulation: photos off
  const diff = shapeDiff(shapes.MOCK[k], shapes.API[k])
  check(`mock / API ${k}: same response structure`, shapes.MOCK[k] && diff.length === 0, diff.join('; '))
}
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
