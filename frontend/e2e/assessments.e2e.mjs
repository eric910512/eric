// Sprint 4 — nursing assessments, in mock mode and API mode (patient P00001, nurse01):
// draft → edit → sign → risk on the nurse dashboard → 護理評估 page → correction (new version,
// original kept as 已被修正) → item follow-up → patient timeline shows only a neutral line →
// 390 px → mock / API response structure.
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
const MOCK_P1 = '72ba2de4-f19d-4daf-96f9-03a5e83c07d0'
const S_TEXT = '病人表示手指麻（E2E 內部主觀）'

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
async function signOut(p) {
  await p.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.innerText.trim() === '登出').click())
  await p.waitForFunction(() => location.pathname === '/login')
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
const listRows = (p) => p.$$eval('[data-assessment-list] [data-assessment]', (els) => els.map((e) => ({ id: e.dataset.assessment, status: e.dataset.signStatus, text: e.innerText })))

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(desk)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })
  const pid = mode === 'API'
    ? (await api('/patients?q=P00001', await tokenFor('nurse01@demo.local'))).body.data[0].id
    : MOCK_P1

  // ---------------------------------------------------------------- draft
  await signIn(p, 'nurse01@demo.local')
  await go(p, `/nurse/patients/${pid}`)
  check(`${mode} nurse: patient page has 護理評估`, await waitSel(p, '[data-assessments-panel]'))
  const before = (await listRows(p)).length
  await clickIn(p, '[data-new-assessment]')
  await p.waitForSelector('[data-assessment-form]')
  await p.select('[data-assessment-form] select[name=assessment_type]', 'pre_chemo')
  await p.select('[data-assessment-form] select[name=risk_level]', 'high')
  await setValue(p, '[data-assessment-form] textarea[name=subjective]', S_TEXT)
  await setValue(p, '[data-assessment-form] textarea[name=plan]', '持續監測神經病變')
  await clickIn(p, '[data-assessment-form] [data-add-item]')
  await setValue(p, '[data-assessment-form] [data-item-row] input[name=item_description]', '周邊神經病變風險')
  await clickIn(p, '[data-assessment-form] button[type=submit]')
  check(`${mode} nurse: draft saved`, await waitText(p, '已儲存草稿'))
  let rows = await listRows(p)
  const draft = rows.find((r) => r.status === 'draft' && r.text.includes('化療前評估'))
  check(`${mode} nurse: list shows the draft with high risk`, rows.length === before + 1 && draft?.text.includes('風險 高'), JSON.stringify(rows.map((r) => r.text.slice(0, 40))))
  await go(p, '/nurse/assessments')
  check(`${mode} nurse: 護理評估 page lists it as 待簽署`, await waitSel(p, '[data-my-drafts]') && (await p.$eval('[data-my-drafts]', (e) => e.innerText)).includes('化療前評估'))

  // ---------------------------------------------------------------- edit + sign
  await go(p, `/nurse/patients/${pid}`)
  await p.waitForSelector(`[data-assessment="${draft.id}"] button`)
  await clickIn(p, `[data-assessment="${draft.id}"] button`)
  await p.waitForSelector('[data-edit-assessment]')
  await clickIn(p, '[data-edit-assessment]')
  await setValue(p, '[data-assessment-form] textarea[name=assessment]', '化療前評估可施打')
  await clickIn(p, '[data-assessment-form] button[type=submit]')
  check(`${mode} nurse: draft edited`, await waitText(p, '已更新草稿'))
  await p.waitForSelector('[data-sign-assessment]')
  await clickIn(p, '[data-sign-assessment]')
  check(`${mode} nurse: signed`, await waitText(p, '已簽署') && await p.waitForSelector(`[data-assessment="${draft.id}"][data-sign-status=signed]`, { timeout: 8000 }).then(() => true, () => false))
  check(`${mode} nurse: signed assessment offers 修正, not 修改`, !(await p.$('[data-edit-assessment]')) && !!(await p.$('[data-amend-assessment]')))
  await p.$eval('[data-item-status]', (el) => { el.value = 'in_progress'; el.dispatchEvent(new Event('change', { bubbles: true })) })
  check(`${mode} nurse: item follow-up after signing`, await waitText(p, '周邊神經病變風險」：處理中'))

  // ---------------------------------------------------------------- risk on the nurse dashboard
  await go(p, `/nurse/${pid}`)
  check(`${mode} nurse dashboard: assessment risk raises the patient risk (existing rule)`, await waitText(p, '護理評估風險：high') && (await text(p)).includes('高風險'))

  // ---------------------------------------------------------------- correction
  await go(p, `/nurse/patients/${pid}`)
  await p.waitForSelector(`[data-assessment="${draft.id}"] button`)
  await clickIn(p, `[data-assessment="${draft.id}"] button`)
  await p.waitForSelector('[data-amend-assessment]')
  await clickIn(p, '[data-amend-assessment]')
  await p.select('[data-assessment-form] select[name=risk_level]', 'medium')
  await clickIn(p, '[data-assessment-form] button[type=submit]')
  check(`${mode} nurse: correction without a reason is rejected`, await waitText(p, '請填寫修正原因'))
  await setValue(p, '[data-assessment-form] input[name=amend_reason]', '風險誤判')
  await clickIn(p, '[data-assessment-form] button[type=submit]')
  check(`${mode} nurse: correction draft created`, await waitText(p, '已建立修正版本'))
  await idle(p)
  rows = await listRows(p)
  const correction = rows.find((r) => r.status === 'draft' && r.text.includes('修正版本'))
  check(`${mode} nurse: original still listed until the correction is signed`, !!correction && rows.some((r) => r.id === draft.id))
  await p.waitForSelector(`[data-assessment="${correction.id}"] [data-sign-assessment], [data-assessment="${correction.id}"] button`)
  if (!(await p.$(`[data-assessment="${correction.id}"] [data-sign-assessment]`))) await clickIn(p, `[data-assessment="${correction.id}"] button`)
  await p.waitForSelector(`[data-assessment="${correction.id}"] [data-sign-assessment]`)
  await clickIn(p, `[data-assessment="${correction.id}"] [data-sign-assessment]`)
  await p.waitForSelector(`[data-assessment="${correction.id}"][data-sign-status=signed]`, { timeout: 8000 }).catch(() => {})
  await p.waitForFunction((id) => !document.querySelector(`[data-assessment="${id}"]`), { timeout: 8000 }, draft.id).catch(() => {})
  rows = await listRows(p)
  check(`${mode} nurse: after signing the correction only the new version is listed (medium)`, !rows.some((r) => r.id === draft.id)
    && rows.some((r) => r.id === correction.id && r.status === 'signed' && r.text.includes('風險 中')), JSON.stringify(rows.map((r) => r.id)))
  check(`${mode} nurse: version history shows the original as 已被修正`, await waitSel(p, `[data-assessment="${correction.id}"] [data-versions]`)
    && (await p.$eval(`[data-assessment="${correction.id}"] [data-versions]`, (e) => e.innerText)).includes('已被修正'))
  await p.setViewport(phone)
  await idle(p)
  check(`${mode} 390px: assessments panel (expanded) has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-assessments.png`, fullPage: true })
  await p.setViewport(desk)

  // ---------------------------------------------------------------- shapes
  if (mode === 'API') {
    const t = await tokenFor('nurse01@demo.local')
    shapes.API.list = shape((await api(`/nursing-assessments?patient_id=${pid}`, t)).body)
    shapes.API.detail = shape((await api(`/nursing-assessments/${correction.id}`, t)).body)
    shapes.API.versions = shape((await api(`/nursing-assessments/${correction.id}/versions`, t)).body)
    shapes.API.patient403 = (await api(`/nursing-assessments?patient_id=me`, await tokenFor('patient01@demo.local'))).status
  } else {
    const m = await p.evaluate(async (pid, id) => {
      const mock = await import('/src/mock/api.js')
      return { list: mock.mockListAssessments(pid), detail: mock.mockGetAssessment(Number(id)), versions: mock.mockAssessmentVersions(Number(id)) }
    }, pid, correction.id)
    for (const [k, v] of Object.entries(m)) shapes.MOCK[k] = shape(v)
  }
  await signOut(p)

  // ---------------------------------------------------------------- patient: neutral line only
  await signIn(p, 'patient01@demo.local')
  await p.waitForFunction(() => location.pathname === '/patient')
  if (mode === 'MOCK') {
    shapes.MOCK.patient403 = await p.evaluate(async () => {
      const mock = await import('/src/mock/api.js')
      try { mock.mockListAssessments('me'); return 200 } catch (e) { return e.status }
    })
  }
  await go(p, '/patient/timeline')
  await waitSel(p, 'section[data-timeline] li[data-event-type]')
  await p.$$eval('section[data-timeline] button[aria-expanded="false"]', (bs) => bs.forEach((b) => b.click()))
  const tl = await p.$eval('section[data-timeline]', (e) => e.innerText)
  check(`${mode} patient timeline: the assessment appears as 護理師化療前評估 with no SOAP / risk`, tl.includes('護理師化療前評估') && tl.includes('護理師已完成評估')
    && !tl.includes(S_TEXT) && !tl.includes('持續監測神經病變') && !tl.includes('風險'))
  await ctx.close()
}

for (const k of ['list', 'detail', 'versions']) {
  const diff = shapeDiff(shapes.API[k], shapes.MOCK[k])
  check(`mock / API ${k}: same response structure`, shapes.API[k] && shapes.MOCK[k] && diff.length === 0, diff.join('; '))
}
check('mock / API: patient → 403 in both', shapes.API.patient403 === 403 && shapes.MOCK.patient403 === 403, `${shapes.API.patient403} / ${shapes.MOCK.patient403}`)
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
