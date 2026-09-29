import puppeteer from 'puppeteer-core'
import { API_URL, CHROME, CHROME_ARGS, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const API = API_URL
const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const idle = (p) => p.waitForNetworkIdle({ idleTime: 400, timeout: 6000 }).catch(() => {})
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })
const errors = []

async function login(base, email, viewport) {
  const ctx = await browser.createBrowserContext()
  const page = await ctx.newPage()
  page.on('pageerror', (e) => errors.push(`${email}: ${e.message}`))
  page.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${email}: ${m.text()}`))
  await page.setViewport(viewport)
  await page.goto(base + '/login', { waitUntil: 'networkidle0' })
  await page.type('input[name=email]', email)
  await page.type('input[name=password]', 'Demo@1234')
  await page.click('button[type=submit]')
  await idle(page)
  return page
}
const api = async (path, token, opts = {}) => {
  const r = await fetch(API + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const tokenFor = async (email) => (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password: 'Demo@1234' }) })).body.data.access_token

const phone = { width: 390, height: 1400, deviceScaleFactor: 1 }
const desk = { width: 1440, height: 1600, deviceScaleFactor: 1 }
const TL = 'section[data-timeline]'
const types = (p) => p.$$eval(`${TL} li[data-event-type]`, (els) => els.map((e) => e.dataset.eventType))
const noOverflow = (p) => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const waitTimeline = (p) => p.waitForFunction((sel) => {
  const el = document.querySelector(sel)
  return el && (el.querySelector('li[data-event-type]') || /沒有/.test(el.innerText)) && !el.querySelector('[aria-busy]')
}, { timeout: 10000 }, TL)
const MS = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/
async function timeFormat(p) {
  const ts = await p.$$eval(`${TL} li[data-occurred-at]`, (els) => els.map((e) => e.dataset.occurredAt))
  return { ts, ms: ts.length > 0 && ts.every((t) => MS.test(t)), ordered: ts.every((t, i) => i === 0 || Date.parse(ts[i - 1]) >= Date.parse(t)) }
}
async function expand(p, predicate) {
  const id = await p.evaluate((sel, src) => {
    const fn = new Function('li', `return (${src})(li)`)
    const li = [...document.querySelectorAll(`${sel} li[data-event-type]`)].find((x) => fn(x))
    if (!li) return null
    li.querySelector('button[aria-expanded]').click()
    return li.dataset.eventId
  }, TL, predicate.toString())
  await sleep(150)
  return id
}
const detailText = (p, id) => p.$eval(`${TL} li[data-event-id="${id}"]`, (el) => el.innerText).catch(() => '')
async function expandAll(p) {
  await p.$$eval(`${TL} button[aria-expanded="false"]`, (bs) => bs.forEach((b) => b.click()))
  await sleep(200)
}

// ================================================================== MOCK
console.log('\n===== MOCK =====')
{
  const base = MOCK_URL
  const p = await login(base, 'patient02@demo.local', phone)
  await p.evaluate(() => [...document.querySelectorAll('nav[aria-label="主要功能"] a')].find((a) => a.innerText.includes('健康紀錄')).click())
  await p.waitForFunction(() => location.pathname === '/patient/timeline')
  await waitTimeline(p)
  const t = new Set(await types(p))
  check('MOCK patient: 健康紀錄 opens the timeline with symptom / vital / lab / notification / chemotherapy events',
    ['SYMPTOM', 'VITAL_SIGN', 'LAB_RESULT', 'NOTIFICATION', 'CHEMOTHERAPY'].every((x) => t.has(x)), [...t].join(','))
  const id = await expand(p, (li) => li.dataset.eventType === 'NOTIFICATION' && li.innerText.includes('噁心'))
  const d = await detailText(p, id)
  check('MOCK patient: expanding an alert shows its message and handling status', d.includes('已處理完成'), d.replace(/\n/g, ' | ').slice(0, 150))
  await expandAll(p)
  const page = await p.$eval(TL, (el) => el.innerText)
  check('MOCK patient: nurse internal note and nurse names are not shown', !page.includes('已電話衛教止吐藥使用時機') && !page.includes('測試護理師'))
  check('MOCK patient: 390px with every event expanded — no horizontal overflow', await noOverflow(p))
  const mf = await timeFormat(p)
  check('MOCK patient: every event time is ISO UTC with milliseconds (…:SS.sssZ), newest → oldest by instant', mf.ms && mf.ordered, mf.ts.slice(0, 3).join(' | '))
  await p.screenshot({ path: `${OUT}/timeline-MOCK-patient.png`, fullPage: true })

  const n = await login(base, 'nurse01@demo.local', desk)
  await n.waitForFunction(() => document.body.innerText.includes('我的個案'))
  // the caseload list renders after its API response, later than the page heading
  await n.waitForFunction(() => [...document.querySelectorAll('section[aria-labelledby="caseload-title"] li button')].some((b) => b.innerText.includes('P00002')), { timeout: 15000 })
  await n.evaluate(() => [...document.querySelectorAll('section[aria-labelledby="caseload-title"] li button')].find((b) => b.innerText.includes('P00002')).click())
  await idle(n)
  await n.waitForSelector(TL); await waitTimeline(n)
  await expandAll(n)
  const nt = await n.$eval(TL, (el) => el.innerText)
  check('MOCK nurse: patient detail has the timeline with internal note and assessment', nt.includes('病人照護時間軸') && nt.includes('已電話衛教止吐藥使用時機')
    && nt.includes('護理評估（電話追蹤）'))
}

// ================================================================== API
console.log('\n===== API =====')
{
  const base = WEB_URL
  const NOTE = '已電話聯繫病人，請病人立即至急診（內部備註）'
  const PLAN = '調整止吐藥服用時間，明日電話追蹤（內部處置）'
  const pt = await tokenFor('patient01@demo.local')
  const nt = await tokenFor('nurse01@demo.local')
  const v = (await api('/vital-signs', pt, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ patient_id: 'me', temperature_c: 38.6, heart_rate_bpm: 92 }) })).body.data
  const fever = (await api('/notifications?status=pending', nt)).body.data.find((x) => x.alert_rule?.code === 'fever')
  await api(`/notifications/${fever.id}/acknowledge`, nt, { method: 'POST' })
  await api(`/notifications/${fever.id}/start`, nt, { method: 'POST' })
  await api(`/notifications/${fever.id}/resolve`, nt, { method: 'POST', body: JSON.stringify({ resolution_note: NOTE }) })
  const pid = fever.patient.id
  await api('/labs/results', nt, { method: 'POST', body: JSON.stringify({ patient_id: pid, collected_at: v.measured_at,
    results: [{ test_code: 'WBC', value: 2.1 }, { test_code: 'ANC', value: 0.8 }] }) })
  const rec = (await api('/symptoms/records/me?review_status=submitted', pt)).body.data[0]
  await api(`/symptoms/records/${rec.id}/review`, nt, { method: 'POST', body: JSON.stringify({ assessment_type: 'phone_follow_up', action_note: PLAN }) })
  const apiOrder = (await api('/patients/me/timeline?limit=50', pt)).body.data.map((e) => e.event_id)

  // ---- patient
  const p = await login(base, 'patient01@demo.local', phone)
  check('API patient login → /patient', new URL(p.url()).pathname === '/patient')
  await p.evaluate(() => [...document.querySelectorAll('nav[aria-label="主要功能"] a')].find((a) => a.innerText.includes('健康紀錄')).click())
  await p.waitForFunction(() => location.pathname === '/patient/timeline')
  await waitTimeline(p)
  check('API patient: 健康紀錄 → 照護時間軸 tab active', await p.evaluate(() => document.querySelector('nav[aria-label="健康紀錄"] a[aria-current="page"]')?.innerText === '照護時間軸'))
  const t = await types(p)
  check('API patient: timeline shows symptom / vital / lab / notification / chemotherapy (+ handling, nursing care)',
    ['SYMPTOM', 'VITAL_SIGN', 'LAB_RESULT', 'NOTIFICATION', 'CHEMOTHERAPY', 'NOTIFICATION_STATUS', 'NURSING_ASSESSMENT'].every((x) => t.includes(x)), [...new Set(t)].join(','))
  const shown = await p.$$eval(`${TL} li[data-event-id]`, (els) => els.map((e) => e.dataset.eventId))
  check('API patient: newest → oldest, same order as the API', shown.join() === apiOrder.slice(0, shown.length).join(), `${shown.length} events`)
  const chemo = await p.$$eval(`${TL} li[data-event-type="CHEMOTHERAPY"]`, (els) => els.map((e) => e.innerText.replace(/\n/g, ' ')))
  check('API patient: chemotherapy shows cycle start (全天) and the administration', chemo.some((x) => x.includes('第 1 次化療開始') && x.includes('全天'))
    && chemo.some((x) => x.includes('Cisplatin')), chemo.join(' || '))
  const labId = await expand(p, (li) => li.dataset.eventType === 'LAB_RESULT' && li.innerText.includes('過低') === false && li.innerText.includes('偏低'))
  const labText = await detailText(p, labId)
  check('API patient: lab detail uses plain names and status words (no reference ranges)', labText.includes('嗜中性白血球（抵抗力）') && labText.includes('偏低')
    && !labText.includes('參考'), labText.replace(/\n/g, ' | ').slice(0, 160))
  const nId = await expand(p, (li) => li.dataset.eventType === 'NOTIFICATION' && li.innerText.includes('體溫'))
  const nText = await detailText(p, nId)
  check('API patient: expanding the alert shows own message + 已處理完成', nText.includes('您的體溫為 38.6°C') && nText.includes('已處理完成'), nText.replace(/\n/g, ' | ').slice(0, 160))
  await expandAll(p)
  const all = await p.$eval(TL, (el) => el.innerText)
  check('API patient: the internal note, assessment plan and nurse names never appear', !all.includes(NOTE) && !all.includes(PLAN) && !all.includes('測試護理師'))
  check('API patient: handling shown in patient wording', all.includes('護理師已接手') && all.includes('護理師正在處理') && all.includes('護理師電話追蹤'))
  check('API patient: 390px with every event expanded — no horizontal overflow', await noOverflow(p))
  const af = await timeFormat(p)
  check('API patient: every event time is ISO UTC with milliseconds (…:SS.sssZ), newest → oldest by instant', af.ms && af.ordered, af.ts.slice(0, 3).join(' | '))
  await p.screenshot({ path: `${OUT}/timeline-API-patient.png`, fullPage: true })

  // date filter: Day 1 only
  const day1 = (await api('/patients/me/timeline?limit=100', pt)).body.data.find((e) => e.title === '第 1 次化療開始').occurred_at
  const local = new Date(new Date(day1).getTime() + 8 * 3600000).toISOString().slice(0, 10)
  await p.evaluate((d) => {
    for (const n of ['timeline-start', 'timeline-end']) {
      const el = document.querySelector(`input[name="${n}"]`)
      el.value = d
      el.dispatchEvent(new Event('input', { bubbles: true }))
    }
  }, local)
  await p.evaluate(() => [...document.querySelectorAll('section[data-timeline] form button')].find((b) => b.innerText === '查看').click())
  await idle(p); await waitTimeline(p)
  const f = await types(p)
  check('API patient: date filter (Day 1) → only that day (chemotherapy + pre-chemo CBC)', f.length === 3 && f.every((x) => ['CHEMOTHERAPY', 'LAB_RESULT'].includes(x)), f.join(','))

  // ---- nurse
  const n = await login(base, 'nurse01@demo.local', desk)
  await n.waitForFunction(() => document.body.innerText.includes('我的個案'))
  await n.waitForSelector(TL); await waitTimeline(n)
  const nt7 = new Set(await types(n))
  // Sprint 3 added APPOINTMENT (the seed's 14:00 appointment appears once its time has come).
  const SEVEN = ['CHEMOTHERAPY', 'SYMPTOM', 'VITAL_SIGN', 'LAB_RESULT', 'NOTIFICATION', 'NOTIFICATION_STATUS', 'NURSING_ASSESSMENT']
  check('API nurse: assigned patient detail shows the timeline with all 7 original event types (plus appointments when due)',
    SEVEN.every((t) => nt7.has(t)) && [...nt7].every((t) => SEVEN.includes(t) || t === 'APPOINTMENT'), [...nt7].join(','))
  await expandAll(n)
  const nurseText = await n.$eval(TL, (el) => el.innerText)
  check('API nurse: sees internal note, assessment plan, who handled it, lab reference ranges',
    nurseText.includes(NOTE) && nurseText.includes(PLAN) && nurseText.includes('測試護理師 林') && nurseText.includes('參考 1.5–7.5'))
  await n.screenshot({ path: `${OUT}/timeline-API-nurse.png`, clip: await n.$eval(TL, (el) => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y + scrollY, width: r.width, height: Math.min(r.height, 1800) } }) })
  const other = await api('/patients/' + '00000000-0000-0000-0000-000000000000' + '/timeline', nt)
  check('API nurse: non-assigned / unknown patient → 404', other.status === 404)
  await n.setViewport(phone); await sleep(300)
  check('API nurse: 390px — no horizontal overflow', await noOverflow(n))
}

check('no page errors / console errors', errors.length === 0, errors.join(' || ').slice(0, 300))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
