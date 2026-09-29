// Sprint 5 — corrections and the review queue, in mock mode and API mode (nurse01):
// 待審清單 → a febrile reading (mock: seeded P00002 38.4 °C; API: P00001 39.1 °C reported now)
// corrected in 紀錄修正 → original kept (修正歷史), its alert closed, nurse dashboard shows the
// corrected value → symptom correction → lab mark-error → 390 px → mock / API structure.
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
const desk = { width: 1440, height: 1400, deviceScaleFactor: 1 }
const phone = { width: 390, height: 1200, deviceScaleFactor: 1 }
const MOCK_P2 = 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e'

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const tokenFor = async (email) => (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password: 'Demo@1234' }) })).body.data.access_token
const text = (p) => p.evaluate(() => document.body.innerText)
const path = (p) => p.evaluate(() => location.pathname)
const waitText = (p, s, timeout = 10000) => p.waitForFunction((s) => document.body.innerText.includes(s), { timeout }, s).then(() => true, () => false)
const waitSel = (p, sel, timeout = 10000) => p.waitForSelector(sel, { timeout }).then(() => true, () => false)
const noOverflow = (p) => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const setValue = (p, selector, value) => p.$eval(selector, (el, value) => {
  el.value = value
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
}, value)
const clickIn = (p, sel) => p.$eval(sel, (el) => el.click())
const go = async (p, to) => {
  await p.evaluate((to) => { history.pushState({}, '', to); dispatchEvent(new PopStateEvent('popstate')) }, to)
  await idle(p)
}
async function signIn(p, email) {
  if (!(await path(p)).startsWith('/login')) await go(p, '/login')
  await p.waitForSelector('input[name=email]')
  await setValue(p, 'input[name=email]', email)
  await setValue(p, 'input[name=password]', 'Demo@1234')
  await p.click('button[type=submit]')
  await idle(p)
}
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
    return [...keys].flatMap((k) => (!(k in a) ? [`${at}.${k}: API only`] : !(k in b) ? [`${at}.${k}: mock only`] : shapeDiff(a[k], b[k], `${at}.${k}`)))
  }
  return a === b ? [] : [`${at}: ${a} vs ${b}`]
}
const shapes = { MOCK: {}, API: {} }
const rows = (p) => p.$$eval('[data-record-list] [data-record]', (els) => els.map((e) => ({ id: e.dataset.record, text: e.innerText })))

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(desk)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })

  let pid, feverText, alertTitle
  if (mode === 'API') {
    const pt = await tokenFor('patient01@demo.local')
    await api('/vital-signs', pt, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ patient_id: 'me', temperature_c: 39.1, heart_rate_bpm: 90 }) })
    pid = (await api('/patients?q=P00001', await tokenFor('nurse01@demo.local'))).body.data[0].id
    feverText = '體溫 39.1°C'
    alertTitle = '化療期間發燒'
  } else {
    pid = MOCK_P2
    feverText = '體溫 38.4°C'
    alertTitle = '疑似嗜中性白血球低下發燒'
  }

  // ---------------------------------------------------------------- 待審清單
  await signIn(p, 'nurse01@demo.local')
  await go(p, '/nurse/reviews')
  check(`${mode} nurse: 待審清單 shows all four queues`, await waitSel(p, '[data-queue="notifications"]')
    && (await p.$$('[data-count]')).length === 4)
  const vitQ = await p.$eval('[data-queue="vitals"]', (e) => e.innerText)
  const notQ = await p.$eval('[data-queue="notifications"]', (e) => e.innerText)
  check(`${mode} nurse: the febrile reading and its alert are waiting`, vitQ.includes(feverText.replace('體溫 ', '')) && notQ.includes(alertTitle), `${vitQ.slice(0, 120)} | ${notQ.slice(0, 120)}`)
  check(`${mode} nurse: pending symptom reviews listed`, (await p.$eval('[data-queue="symptoms"]', (e) => e.innerText)).includes('P0000'))

  // ---------------------------------------------------------------- correct the vital
  await go(p, `/nurse/patients/${pid}`)
  await waitSel(p, '[data-records-panel]')
  await clickIn(p, '[data-records-panel] [data-tab="vital"]')
  await p.waitForFunction(() => document.querySelector('[data-record-list="vital"] [data-record]'), { timeout: 8000 }).catch(() => {})
  let list = await rows(p)
  const fever = list.find((r) => r.text.includes(feverText))
  check(`${mode} nurse: 紀錄修正 lists the febrile reading`, !!fever, JSON.stringify(list.map((r) => r.text.slice(0, 50))))
  await clickIn(p, `[data-record="${fever.id}"] [data-amend-record]`)
  await p.waitForSelector('[data-record-form] input[name=temperature_c]')
  await setValue(p, '[data-record-form] input[name=temperature_c]', '36.9')
  await setValue(p, '[data-record-form] input[name=reason]', '輸入錯誤')
  await clickIn(p, '[data-record-form] button[type=submit]')
  check(`${mode} nurse: vital corrected`, await waitText(p, '已更正；原紀錄保留在修正歷史中'))
  await p.waitForFunction((id) => !document.querySelector(`[data-record="${id}"]`), { timeout: 8000 }, fever.id).catch(() => {})
  list = await rows(p)
  const fixed = list.find((r) => r.text.includes('體溫 36.9°C') && r.text.includes('已更正版本'))
  check(`${mode} nurse: list shows the corrected reading, not the original`, !!fixed && !list.some((r) => r.id === fever.id))
  await clickIn(p, `[data-record="${fixed.id}"] [data-history]`)
  check(`${mode} nurse: 修正歷史 keeps the original`, await waitSel(p, `[data-record="${fixed.id}"] [data-record-history]`)
    && (await p.$eval(`[data-record="${fixed.id}"] [data-record-history]`, (e) => e.innerText)).includes(`${feverText}`)
    && (await p.$eval(`[data-record="${fixed.id}"] [data-record-history]`, (e) => e.innerText)).includes('已被更正'))

  await go(p, '/nurse/reviews')
  await waitSel(p, '[data-queue="notifications"]')
  check(`${mode} nurse: the alert raised by the wrong value is no longer waiting`, !(await p.$eval('[data-queue="notifications"]', (e) => e.innerText)).includes(alertTitle)
    && !(await p.$eval('[data-queue="vitals"]', (e) => e.innerText)).includes(feverText.replace('體溫 ', '')))
  await go(p, `/nurse/${pid}`)
  check(`${mode} nurse dashboard: latest vitals show the corrected temperature`, await waitText(p, '36.9'))

  // ---------------------------------------------------------------- symptom correction
  await go(p, `/nurse/patients/${pid}`)
  await waitSel(p, '[data-records-panel]')
  await p.waitForFunction(() => document.querySelector('[data-record-list="symptom"] [data-record]'), { timeout: 8000 }).catch(() => {})
  list = await rows(p)
  const sym = list[0]
  await clickIn(p, `[data-record="${sym.id}"] [data-amend-record]`)
  await p.waitForSelector('[data-record-form] select[name=value_pain]')
  await p.select('[data-record-form] select[name=value_pain]', '1')
  await clickIn(p, '[data-record-form] button[type=submit]')
  check(`${mode} nurse: correction needs a reason (button disabled until filled)`, !!(await p.$('[data-record-form] button[type=submit][disabled]')))
  await setValue(p, '[data-record-form] input[name=reason]', '病人按錯')
  await clickIn(p, '[data-record-form] button[type=submit]')
  check(`${mode} nurse: symptom corrected`, await waitText(p, '已更正；原紀錄保留在修正歷史中')
    && await p.waitForFunction(() => [...document.querySelectorAll('[data-record-list="symptom"] [data-record]')].some((e) => e.innerText.includes('疼痛 1') && e.innerText.includes('已更正版本')), { timeout: 8000 }).then(() => true, () => false))

  // ---------------------------------------------------------------- lab mark-error
  await clickIn(p, '[data-records-panel] [data-tab="lab"]')
  await p.waitForFunction(() => document.querySelector('[data-record-list="lab"] [data-record]'), { timeout: 8000 }).catch(() => {})
  list = await rows(p)
  const lab = list[0]
  await clickIn(p, `[data-record="${lab.id}"] [data-error-record]`)
  await setValue(p, '[data-record-form] input[name=reason]', '登錄到錯的病人')
  await clickIn(p, '[data-record-form] button[type=submit]')
  check(`${mode} nurse: lab marked as error and removed from the list`, await waitText(p, '已標示為錯誤')
    && await p.waitForFunction((id) => !document.querySelector(`[data-record-list="lab"] [data-record="${id}"]`), { timeout: 8000 }, lab.id).then(() => true, () => false))

  // ---------------------------------------------------------------- 390 px
  await p.setViewport(phone)
  await clickIn(p, '[data-records-panel] [data-tab="vital"]')
  await idle(p)
  check(`${mode} 390px: 紀錄修正 has no horizontal overflow`, await noOverflow(p))
  await go(p, '/nurse/reviews')
  await waitSel(p, '[data-queue="labs"]')
  check(`${mode} 390px: 待審清單 has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-review-queue.png`, fullPage: true })
  await p.setViewport(desk)

  // ---------------------------------------------------------------- shapes
  if (mode === 'API') {
    const t = await tokenFor('nurse01@demo.local')
    shapes.API.vitals = shape((await api(`/vital-signs/patient/${pid}`, t)).body)
    shapes.API.history = shape((await api(`/vital-signs/${fixed.id}/history`, t)).body)
    shapes.API.pending = shape((await api('/dashboard/widgets/pending-symptom-reviews/data', t)).body)
  } else {
    const m = await p.evaluate(async (pid, id) => {
      const mock = await import('/src/mock/api.js')
      return { vitals: mock.mockListPatientVitals(pid), history: mock.mockVitalHistory(Number(id)), pending: mock.mockPendingReviews() }
    }, pid, fixed.id)
    for (const [k, v] of Object.entries(m)) shapes.MOCK[k] = shape(v)
  }
  await ctx.close()
}

for (const k of ['vitals', 'history', 'pending']) {
  const diff = shapeDiff(shapes.API[k], shapes.MOCK[k])
  check(`mock / API ${k}: same response structure`, shapes.API[k] && shapes.MOCK[k] && diff.length === 0, diff.join('; '))
}
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
