// 每日症狀與自我照護回報 (rt_daily_report), in mock mode and API mode: patient (390 px) opens the card on
// 症狀回報 → both sections and every question exactly as given → missing answers are flagged → 「沒有」 on a
// self-care question shows the reminder but still submits → 今天已回報 → a second report the same day is
// rejected (409 ALREADY_REPORTED_TODAY) and the card stays 今天已回報 after a reload → 我的回報 shows option
// labels → nurse sees the report (form name, date/time, every answer, 0–10 ≥ 7 marked) and the 待審清單
// summary with option labels → mock / API response structure.
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
const phone = { width: 390, height: 1400, deviceScaleFactor: 1 }
const MOCK_P1 = '72ba2de4-f19d-4daf-96f9-03a5e83c07d0'
const REMINDER = '這是每天重要的自我照護，請記得確實做到。'
const QUESTIONS = [
  ['rt_symptom_24h', '疼痛－放射線皮膚發紅、脫皮導致'],
  ['rt_symptom_24h', '疼痛－口腔黏膜紅腫、發炎導致'],
  ['rt_symptom_24h', '放射線皮膚炎－發紅情形'],
  ['rt_symptom_24h', '放射線皮膚炎－脫皮、脫屑情形'],
  ['rt_symptom_24h', '食慾不佳'],
  ['rt_symptom_24h', '疲倦'],
  ['daily_self_care', '我今天擦保濕乳液或醫師開的藥膏了嗎？'],
  ['daily_self_care', '我今天洗完澡有用毛巾「按壓」，沒有來回摩擦皮膚嗎？'],
  ['daily_self_care', '除了睡覺以外，我有每個小時，以及飯後都有確實漱口嗎？'],
]
const ANSWERS = { rt_pain_skin: 3, rt_pain_oral: 8, rt_dermatitis_redness: 'dark', rt_dermatitis_desquamation: 'moist', rt_appetite_poor: 7,
  rt_fatigue: 4, self_care_moisturizer: 'yes', self_care_towel_pat: 'no', self_care_mouth_rinse: 'yes' }

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const tokenFor = async (email) => (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password: 'Demo@1234' }) })).body.data.access_token
const text = (p, sel = 'body') => p.$eval(sel, (e) => e.innerText).catch(() => '')
const waitSel = (p, sel, timeout = 10000) => p.waitForSelector(sel, { timeout }).then(() => true, () => false)
const waitText = (p, s, timeout = 10000) => p.waitForFunction((s) => document.body.innerText.includes(s), { timeout }, s).then(() => true, () => false)
const waitFn = (p, fn, arg, timeout = 10000) => p.waitForFunction(fn, { timeout }, arg).then(() => true, () => false)
const noOverflow = (p) => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
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
    return [...keys].flatMap((k) => (!(k in a) ? [`${at}.${k}: API only`] : !(k in b) ? [`${at}.${k}: mock only`] : shapeDiff(a[k], b[k], `${at}.${k}`)))
  }
  return a === b ? [] : [`${at}: ${a} vs ${b}`]
}
const shapes = { MOCK: {}, API: {} }

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource|status of 409/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(phone)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })
  let pid = MOCK_P1
  let pt, nt
  if (mode === 'API') {
    pt = await tokenFor('patient01@demo.local')
    nt = await tokenFor('nurse01@demo.local')
    pid = (await api('/patients?q=P00001', nt)).body.data[0].id
  }
  const mockCall = (name, args) => p.evaluate(async (a) => {
    try {
      return { status: 200, body: await (await import('/src/mock/api.js'))[a.name](...a.args) }
    } catch (e) {
      return { status: e.status ?? 500, body: { error: { code: e.code, message: e.message, details: e.details } } }
    }
  }, { name, args })

  // ---------------------------------------------------------------- patient
  await signIn(p, 'patient01@demo.local')
  await go(p, '/patient/symptoms')
  check(`${mode} patient: 症狀回報 shows 每日症狀與自我照護回報 (not yet reported)`, await waitSel(p, '[data-daily-report][data-state=idle] [data-daily-open]')
    && (await text(p, '[data-daily-report]')).includes('每日症狀與自我照護回報'))
  check(`${mode} the existing 今天的症狀 card is still there`, (await text(p)).includes('今天的症狀'))
  await click(p, '[data-daily-open]')
  await waitSel(p, '[data-daily-form] [data-section]')
  const sections = await p.$$eval('[data-daily-form] [data-section]', (s) => s.map((x) => [x.dataset.section, x.querySelector('h3').innerText]))
  check(`${mode} two sections: 過去 24 小時症狀自主管理, 我的每日自評`, JSON.stringify(sections) === JSON.stringify([
    ['rt_symptom_24h', '過去 24 小時症狀自主管理'], ['daily_self_care', '我的每日自評']]), JSON.stringify(sections))
  const asked = await p.$$eval('[data-daily-form] [data-question]', (q) => q.map((x) => [x.closest('[data-section]').dataset.section, x.querySelector('legend').childNodes[0].textContent.trim()])) // visible text (not the sr-only 必填)
  check(`${mode} nine questions, texts and order exactly as given`, JSON.stringify(asked) === JSON.stringify(QUESTIONS), JSON.stringify(asked))
  const opts = await p.$$eval('[data-question=rt_dermatitis_desquamation] input', (i) => i.map((x) => x.nextElementSibling.innerText))
  const yesNo = await p.$$eval('[data-question=self_care_moisturizer] input', (i) => i.map((x) => x.nextElementSibling.innerText))
  check(`${mode} options in the given order (脫皮脫屑; 有 / 沒有)`, opts.join('|') === '無|乾燥、有脫皮脫屑|潮濕、有脫皮脫屑|有脫皮脫屑伴出血' && yesNo.join('|') === '有|沒有', `${opts} / ${yesNo}`)
  const scalePoints = await p.$$eval('[data-question=rt_pain_skin] input', (i) => i.map((x) => x.value))
  check(`${mode} 0–10 questions offer 0…10`, scalePoints.join(',') === '0,1,2,3,4,5,6,7,8,9,10', scalePoints.join(','))

  await click(p, '[data-daily-submit]')
  check(`${mode} submitting with no answers → every question flagged, nothing sent`, await waitSel(p, '[data-daily-missing]')
    && (await text(p, '[data-daily-missing]')).includes('9 題'))
  for (const [code, value] of Object.entries(ANSWERS)) await answer(p, code, value)
  check(`${mode} 「沒有」 on a self-care question → reminder shown`, await waitFn(p, (r) =>
    document.querySelector('[data-question=self_care_towel_pat] [data-field-hint]')?.innerText === r, REMINDER))
  check(`${mode} 「有」 shows no reminder`, !(await p.$('[data-question=self_care_moisturizer] [data-field-hint]')))
  check(`${mode} 390px: form has no horizontal overflow`, await noOverflow(p))
  await new Promise((r) => setTimeout(r, 600)) // let the answer buttons finish their colour transition
  await p.screenshot({ path: `${OUT}/m390-${mode}-rt-daily-form.png`, fullPage: true })
  await click(p, '[data-daily-submit]')
  check(`${mode} submit with the reminder still showing → 今天已回報`, await waitSel(p, '[data-daily-report][data-state=done] [data-daily-done]')
    && (await text(p, '[data-daily-done]')).includes('今天已回報') && !(await p.$('[data-daily-open]')))
  check(`${mode} 我的回報 lists it with option labels`, await waitText(p, '放射線皮膚炎－發紅情形：深紅') && (await text(p, '[data-my-reports]')).includes('疼痛－口腔黏膜紅腫、發炎導致 8'))

  // a second report the same day
  const body = { patient_id: mode === 'API' ? 'me' : pid, form_code: 'rt_daily_report',
    values: Object.entries(ANSWERS).map(([k, v]) => ({ definition_code: k, [typeof v === 'number' ? 'value_numeric' : 'option_code']: v })) }
  const again = mode === 'API'
    ? await api('/symptoms/records', pt, { method: 'POST', body: JSON.stringify(body), headers: { 'Idempotency-Key': crypto.randomUUID() } })
    : await mockCall('submitMockReport', [pid, body])
  check(`${mode} second report the same day → 409 ALREADY_REPORTED_TODAY (今天已回報)`, again.status === 409
    && again.body.error.code === 'ALREADY_REPORTED_TODAY' && again.body.error.message === '今天已回報', JSON.stringify(again.body?.error))
  await go(p, '/patient')
  await go(p, '/patient/symptoms')
  check(`${mode} back on the page the card still says 今天已回報`, await waitSel(p, '[data-daily-report][data-state=done]'))

  // shapes
  const formRes = mode === 'API' ? (await api('/symptoms/forms/rt_daily_report', pt)).body.data : (await p.evaluate(async () => (await import('/src/mock/api.js')).mockForms.rt_daily_report))
  shapes[mode].form = shape(formRes)
  const recs = mode === 'API' ? (await api('/symptoms/records/me?form_code=rt_daily_report', pt)).body : (await mockCall('mockListRecords', [pid, { formCode: 'rt_daily_report' }])).body
  shapes[mode].record = shape(recs.data[0])
  check(`${mode} form_code filter → only this form's reports`, recs.data.length === 1 && recs.data[0].form.code === 'rt_daily_report')
  await signOut(p)

  // ---------------------------------------------------------------- nurse
  await p.setViewport(desk)
  await signIn(p, 'nurse01@demo.local')
  await go(p, `/nurse/${pid}`)
  const card = '[data-record-form]'
  check(`${mode} nurse: the patient's report shows as 每日症狀與自我照護回報`, await waitSel(p, card, 15000))
  const article = await p.evaluateHandle(() => document.querySelector('[data-record-form]').closest('article'))
  const content = await article.evaluate((a) => a.innerText)
  const marked = await article.evaluate((a) => [...a.querySelectorAll('[data-attention]')].map((x) => x.innerText))
  const choices = await article.evaluate((a) => [...a.querySelectorAll('[data-choice-answer]')].map((x) => x.innerText))
  check(`${mode} nurse: every answer (0–10 scores and option labels)`, content.includes('疼痛－放射線皮膚發紅、脫皮導致 3') && content.includes('疲倦 4')
    && choices.includes('放射線皮膚炎－發紅情形：深紅') && choices.includes('放射線皮膚炎－脫皮、脫屑情形：潮濕、有脫皮脫屑')
    && choices.includes('我今天洗完澡有用毛巾「按壓」，沒有來回摩擦皮膚嗎？：沒有') && choices.length === 5, JSON.stringify(choices))
  check(`${mode} nurse: 0–10 answers ≥ 7 marked as needing attention (and only those)`, marked.length === 2
    && marked.some((m) => m.includes('疼痛－口腔黏膜紅腫、發炎導致 8')) && marked.some((m) => m.includes('食慾不佳 7')), JSON.stringify(marked))
  check(`${mode} nurse: report date / time shown`, /\d{1,2}:\d{2}/.test(content), content.slice(0, 60))
  await go(p, '/nurse/reviews')
  check(`${mode} 待審清單: patient + summary with option labels`, await waitFn(p, () => [...document.querySelectorAll('[data-queue=symptoms] li')]
    .some((li) => li.innerText.includes('測試病人 甲') && li.innerText.includes('放射線皮膚炎－發紅情形：深紅'))))
  await signOut(p)
  await ctx.close()
}

for (const k of ['form', 'record']) {
  const diff = shapeDiff(shapes.API[k], shapes.MOCK[k])
  check(`mock / API ${k}: same response structure`, shapes.API[k] && shapes.MOCK[k] && diff.length === 0, diff.join('; '))
}
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
