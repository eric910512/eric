// Patient home redesign (需求列表 2026-10), in mock mode and API mode at 390 px:
// 每日症狀與自我照護回報 entry on top → not reported → link to 症狀回報 (#daily-report) → report →
// home shows 今日已回報 (green) → self-care stamp cards for every day of this month (有 = red ✓,
// 沒有 = red X, not reported = empty, never X) → trend still at the bottom without the self-care
// answers, y axis 0–10 in steps of 1 → no 今天的症狀 / 我的通知 on the home page → 其他症狀回報 and
// 通知 tabs work → 放射線治療進度 from radiotherapy sessions → 今日行程 radiation note →
// nurse 處理項目 (exactly the four) → cancel keeps the alert open → choose + 標示為已處理 resolves it
// → the patient never sees the note → patient / nurse isolation (backend / mock access control).
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
const phone = { width: 390, height: 844, deviceScaleFactor: 1 }
const MOCK_P1 = '72ba2de4-f19d-4daf-96f9-03a5e83c07d0'
const TZ = 'Asia/Taipei'
const SELF_CARE = ['self_care_moisturizer', 'self_care_towel_pat', 'self_care_mouth_rinse']
const ANSWERS = { rt_pain_skin: 3, rt_pain_oral: 2, rt_dermatitis_redness: 'light', rt_dermatitis_desquamation: 'none', rt_appetite_poor: 1,
  rt_fatigue: 4, self_care_moisturizer: 'yes', self_care_towel_pat: 'no', self_care_mouth_rinse: 'yes' }
const HANDLING = ['已協助掛號，請明日至門診報到。', '建議立即前往急診。', '已給予護理衛教。', '已電話聯繫病人。']
const RADIATION_NOTE = '療程期間，週一至週五接受放射線治療，國定假日另行公告'
const CRITICAL_RGB = 'rgb(192, 54, 44)' // --color-critical

const dayFmt = new Intl.DateTimeFormat('en-CA', { timeZone: TZ })
const localToday = () => dayFmt.format(new Date())
const daysInMonth = (day) => { const [y, m] = day.split('-').map(Number); return new Date(Date.UTC(y, m, 0)).getUTCDate() }
const addDays = (day, n) => { const d = new Date(`${day}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10) }
const atLocal = (day, hhmm) => new Date(`${day}T${hhmm}:00+08:00`).toISOString()

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const tokenFor = async (email) => (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password: 'Demo@1234' }) })).body.data.access_token
const text = (p, sel = 'body') => p.$eval(sel, (e) => e.innerText).catch(() => '')
const waitSel = (p, sel, timeout = 10000) => p.waitForSelector(sel, { timeout }).then(() => true, () => false)
const waitFn = (p, fn, arg, timeout = 10000) => p.waitForFunction(fn, { timeout }, arg).then(() => true, () => false)
const noOverflow = (p) => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const setValue = (p, selector, value) => p.$eval(selector, (el, value) => {
  el.value = value
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
}, value)
const click = (p, sel) => p.$eval(sel, (b) => b.click())
const clickText = (p, scope, label) => p.evaluate((scope, label) => {
  const b = [...document.querySelectorAll(`${scope} button, ${scope} a`)].find((x) => x.innerText.trim().split(/\n/)[0].trim() === label) // nav: unread badge on a 2nd line
  b?.click()
  return !!b
}, scope, label)
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
const stampCell = (p, code, day) => p.$eval(`[data-stamp-card=${code}] [data-date="${day}"]`, (li) => ({
  answer: li.dataset.answer, mark: li.querySelector('[data-mark]').innerText.trim(), color: getComputedStyle(li.querySelector('[data-mark]')).color,
})).catch(() => null)
const shot = async (p, sel, name) => {
  const clip = await p.$eval(sel, (el) => { const r = el.getBoundingClientRect(); return { x: 0, y: r.y + scrollY, width: innerWidth, height: Math.ceil(r.height) } }).catch(() => null)
  if (clip) await p.screenshot({ path: `${OUT}/${name}.png`, clip, captureBeyondViewport: true })
}

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(phone)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })
  const mockCall = (name, args) => p.evaluate(async (a) => {
    try {
      return { status: 200, body: await (await import('/src/mock/api.js'))[a.name](...a.args) }
    } catch (e) {
      return { status: e.status ?? 500, body: { error: { code: e.code, message: e.message } } }
    }
  }, { name, args })
  const today = localToday()
  let pid = MOCK_P1
  let pt, nt

  // ---------------------------------------------------------------- setup: radiotherapy sessions (nurse)
  if (mode === 'API') {
    nt = await tokenFor('nurse01@demo.local')
    pt = await tokenFor('patient01@demo.local')
    pid = (await api('/patients?q=P00001', nt)).body.data[0].id
    const ids = []
    for (const n of [0, 1, 2]) {
      const r = await api('/chemotherapy/appointments', nt, { method: 'POST', body: JSON.stringify({ patient_id: pid, appointment_type: 'radiotherapy',
        title: '放射線治療', scheduled_at: atLocal(addDays(today, n), '00:05'), location: '放射腫瘤科（Demo）' }) })
      ids.push(r.body?.data?.id)
    }
    const ci = await api(`/chemotherapy/appointments/${ids[0]}/check-in`, nt, { method: 'POST' })
    const done = await api(`/chemotherapy/appointments/${ids[0]}/complete`, nt, { method: 'POST' })
    check(`${mode} setup: 3 radiotherapy sessions, today's completed`, ids.every(Boolean) && ci.status === 200 && done.status === 200, `${ci.status}/${done.status}`)
    // an other-day fever report → open risk alert for nurse01 (for the 處理項目 part)
    await api('/symptoms/records', pt, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ patient_id: 'me',
      form_code: 'daily_chemo_check', values: [{ definition_code: 'pain', value_numeric: 1 }, { definition_code: 'nausea', value_numeric: 0 },
        { definition_code: 'fatigue', value_numeric: 1 }, { definition_code: 'fever', value_boolean: true }] }) })
  } else {
    await signIn(p, 'nurse01@demo.local')
    const made = []
    for (const n of [1, 2, 3]) {
      made.push(await mockCall('mockCreateAppointment', [{ patient_id: pid, appointment_type: 'radiotherapy', title: '放射線治療',
        scheduled_at: atLocal(addDays(today, n), '09:00'), location: '放射腫瘤科（Demo）' }]))
    }
    check(`${mode} setup: 3 radiotherapy sessions`, made.every((r) => r.status === 200), JSON.stringify(made.map((r) => r.status)))
    await signOut(p)
  }

  // ---------------------------------------------------------------- patient home before reporting
  await signIn(p, 'patient01@demo.local')
  await waitSel(p, '[data-daily-entry]')
  await waitSel(p, '#w-symptom-trend')
  const order = await p.$$eval('main [id^=w-]', (els) => els.map((e) => e.id.slice(2)))
  check(`${mode} 1. home shows the daily report entry, first on the page`, order[0] === 'daily-report', order.join(' > '))
  check(`${mode} 3. not reported → 未完成 state with a link (no 今日已回報)`, await p.$eval('[data-daily-entry]', (e) => e.dataset.reported) === 'false'
    && !!(await p.$('[data-daily-entry-link]')) && !(await text(p, '[data-daily-entry]')).includes('今日已回報'))
  check(`${mode} 10. trend still on the home page, at the bottom`, order.at(-1) === 'symptom-trend', order.join(' > '))
  check(`${mode} 16. 放射線治療進度 right after the entry block, before 今日行程`,
    order.indexOf('radiation-progress') > 0 && order.indexOf('radiation-progress') < order.indexOf('today-schedule'), order.join(' > '))
  const ticks = await p.$$eval('#w-symptom-trend svg text[text-anchor=end]', (ts) => ts.map((t) => t.textContent.trim()))
  check(`${mode} 11. trend y axis 0–10, one tick per point`, JSON.stringify(ticks) === JSON.stringify(['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10']), ticks.join(','))
  const home = await text(p, 'main')
  const headings = await p.$$eval('main h2', (hs) => hs.map((h) => h.innerText.trim()))
  check(`${mode} 12. no 今天的症狀 card (疼痛 / 噁心 / 疲倦 / 發燒) on the home page`, !headings.includes('今天的症狀') && !(await p.$('#w-symptom-quick-report'))
    && !(await p.$('main fieldset')), headings.join(' | '))
  check(`${mode} 14. no 我的通知 on the home page`, !home.includes('我的通知') && !(await p.$('#w-my-notifications')))
  const rp = await text(p, '#w-radiation-progress')
  check(`${mode} 16. 放射線治療進度 from the radiotherapy sessions`, rp.includes('放射線治療進度') && (mode === 'API'
    ? /已完成\s*1\s*\/ 共 3 次/.test(rp) && rp.includes('今天') && rp.includes('下一次') : /已完成\s*0\s*\/ 共 3 次/.test(rp) && rp.includes('下一次')), rp.replace(/\n/g, ' | '))
  check(`${mode} 17. 今日行程 shows the radiation schedule note`, (await text(p, '#w-today-schedule')).includes(RADIATION_NOTE))
  const nDays = daysInMonth(today)
  const cards = await p.$$eval('[data-stamp-card]', (cs) => cs.map((c) => ({ code: c.dataset.stampCard, days: c.querySelectorAll('[data-date]').length })))
  check(`${mode} 5. three stamp cards, one cell per day of this month (${nDays})`,
    JSON.stringify(cards.map((c) => c.code)) === JSON.stringify(SELF_CARE) && cards.every((c) => c.days === nDays), JSON.stringify(cards))
  const firstLabel = await p.$eval(`[data-stamp-card] [data-date="${today.slice(0, 8)}01"]`, (li) => li.innerText.trim()).catch(() => '')
  check(`${mode} 5. dates read like ${Number(today.slice(5, 7))}/1`, firstLabel.startsWith(`${Number(today.slice(5, 7))}/1`), firstLabel)
  const before = await Promise.all(SELF_CARE.map((c) => stampCell(p, c, today)))
  check(`${mode} 8. not reported today → empty cell, not X`, before.every((c) => c?.answer === 'none' && c.mark === ''), JSON.stringify(before))
  const legend = await text(p, '#w-symptom-trend ul')
  check(`${mode} 9. trend legend has none of the self-care questions`, !/保濕|毛巾|漱口/.test(legend), legend.replace(/\n/g, ' | '))
  check(`${mode} 390 px: home has no horizontal overflow`, await noOverflow(p))
  const cell = await p.$eval('[data-stamp-card] [data-date]', (li) => li.getBoundingClientRect().width)
  check(`${mode} 390 px: stamp cells are readable (≥ 36 px)`, cell >= 36, `${cell.toFixed(1)} px`)
  await p.screenshot({ path: `${OUT}/home-${mode}-top.png` })

  // ---------------------------------------------------------------- nav: 其他症狀回報 + 通知
  const navLabels = await p.$$eval('nav[aria-label=主要功能] a', (as) => as.map((a) => a.innerText.trim().split(/\n/)[0].trim()))
  check(`${mode} 13/15. bottom nav: 其他症狀回報 and 通知`, navLabels.includes('其他症狀回報') && navLabels.includes('通知') && !navLabels.includes('症狀回報'), navLabels.join(','))
  const navFits = await p.$$eval('nav[aria-label=主要功能] a > span', (ss) => ss.every((s) => s.getClientRects().length === 1 && s.getBoundingClientRect().right <= innerWidth))
  check(`${mode} 390 px: nav labels fit on one line`, navFits)
  await clickText(p, 'nav[aria-label=主要功能]', '通知')
  check(`${mode} 15. 通知 tab opens the notification page`, await waitFn(p, () => location.pathname === '/patient/notifications'))
  await clickText(p, 'nav[aria-label=主要功能]', '其他症狀回報')
  check(`${mode} 13. 其他症狀回報 → 症狀回報 with 今天的症狀`, await waitFn(p, () => location.pathname === '/patient/symptoms')
    && await waitFn(p, () => document.body.innerText.includes('今天的症狀')))
  await clickText(p, 'nav[aria-label=主要功能]', '首頁')
  await waitSel(p, '[data-daily-entry-link]')

  // ---------------------------------------------------------------- entry → 症狀回報 → report
  await click(p, '[data-daily-entry-link]')
  check(`${mode} 2. entry → /patient/symptoms#daily-report`, await waitFn(p, () => location.pathname === '/patient/symptoms' && location.hash === '#daily-report'))
  check(`${mode} 2. the daily report card is there, ready to start`, await waitSel(p, '[data-daily-report][data-state=idle] [data-daily-open]'))
  await click(p, '[data-daily-open]')
  await waitSel(p, '[data-daily-form] [data-question]')
  for (const [code, v] of Object.entries(ANSWERS)) {
    if (typeof v === 'number') await setValue(p, `[data-question=${code}] input[type=range]`, String(v)).catch(() => {})
    await p.$eval(`[data-question=${code}] input[value="${v}"]`, (r) => r.click()).catch(() => {})
  }
  await click(p, '[data-daily-submit]')
  check(`${mode} report submitted (今天已回報 on 症狀回報)`, await waitSel(p, '[data-daily-report][data-state=done]'))

  await clickText(p, 'nav[aria-label=主要功能]', '首頁')
  check(`${mode} 4. back home → 今日已回報`, await waitFn(p, () => document.querySelector('[data-daily-entry]')?.dataset.reported === 'true')
    && (await text(p, '[data-daily-entry]')).includes('今日已回報') && !(await p.$('[data-daily-entry-link]')))
  const okColor = await p.$eval('[data-daily-entry] [role=status]', (e) => getComputedStyle(e).backgroundColor).catch(() => '')
  check(`${mode} 4. 今日已回報 is green`, okColor === 'rgb(225, 242, 234)', okColor)
  await waitSel(p, `[data-stamp-card=self_care_towel_pat] [data-date="${today}"][data-answer=no]`)
  const after = Object.fromEntries(await Promise.all(SELF_CARE.map(async (c) => [c, await stampCell(p, c, today)])))
  check(`${mode} 6. 有 → red ✓`, after.self_care_moisturizer?.mark === '✓' && after.self_care_mouth_rinse?.mark === '✓'
    && after.self_care_moisturizer.color === CRITICAL_RGB, JSON.stringify(after.self_care_moisturizer))
  check(`${mode} 7. 沒有 → red X`, after.self_care_towel_pat?.answer === 'no' && after.self_care_towel_pat.mark === 'X'
    && after.self_care_towel_pat.color === CRITICAL_RGB, JSON.stringify(after.self_care_towel_pat))
  const others = await p.$$eval(`[data-stamp-card] [data-date]:not([data-date="${today}"])`, (ls) => ls.filter((l) => l.dataset.answer !== 'none').map((l) => l.dataset.date))
  check(`${mode} 8. days without a report stay empty`, mode === 'API' ? others.length === 0 : true, others.join(','))
  const legend2 = await text(p, '#w-symptom-trend ul')
  check(`${mode} 9. after reporting, the trend still has no self-care line`, !/保濕|毛巾|漱口/.test(legend2), legend2.replace(/\n/g, ' | '))
  check(`${mode} 390 px: still no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/home-${mode}-reported.png` })
  await shot(p, '#w-self-care-stamps', `home-${mode}-stamps`)
  await shot(p, '#w-symptom-trend', `home-${mode}-trend`)
  await p.screenshot({ path: `${OUT}/home-${mode}-full.png`, fullPage: true })

  // ---------------------------------------------------------------- isolation (backend / mock access control)
  if (mode === 'API') {
    const at = await tokenFor('admin01@demo.local')
    const other = (await api('/patients', at, { method: 'POST', body: JSON.stringify({ display_name: '隔離測試', gender: 'female', date_of_birth: '1970-01-01' }) })).body.data
    const own = (await api('/dashboard/patient/me', pt)).body.data.widgets
    check(`${mode} 20. patient: own stamps via "me"`, own['self-care-stamps'].items.length === 3 && own['daily-report'].reported_today === true)
    check(`${mode} 20. patient A → patient B's dashboard 404`, (await api(`/dashboard/patient/${other.id}`, pt)).status === 404)
    check(`${mode} 20. nurse → unassigned patient's dashboard 404`, (await api(`/dashboard/patient/${other.id}`, nt)).status === 404)
    check(`${mode} 20. nurse → assigned patient's stamps = the patient's`,
      JSON.stringify((await api(`/dashboard/patient/${pid}`, nt)).body.data.widgets['self-care-stamps']) === JSON.stringify(own['self-care-stamps']))
  } else {
    const otherMock = 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e' // patient02
    check(`${mode} 20. patient A → patient B's dashboard 404`, (await mockCall('mockPatientDashboard', [otherMock])).status === 404)
    check(`${mode} 20. patient: own dashboard has the stamps`, (await mockCall('mockPatientDashboard', [MOCK_P1])).body?.widgets?.['self-care-stamps']?.items?.length === 3)
  }
  await signOut(p)

  // ---------------------------------------------------------------- nurse: 處理項目
  await p.setViewport(phone)
  await signIn(p, 'nurse01@demo.local')
  if (mode === 'MOCK') {
    const unassigned = (await mockCall('mockCreatePatient', [{ display_name: '隔離測試 MOCK', gender: 'female', date_of_birth: '1970-01-01' }])).body
    // patients created by a nurse are assigned to that nurse; an admin-created one is not
    if (unassigned?.data?.id) {
      const r = await mockCall('mockPatientDashboard', [unassigned.data.id])
      check(`${mode} 20. nurse sees only assigned patients (created → assigned or 404)`, r.status === 200 || r.status === 404, String(r.status))
    }
  }
  await go(p, '/nurse')
  const hasHandle = await waitFn(p, () => [...document.querySelectorAll('section[aria-label=未處理通知] button')].some((b) => b.innerText.trim() === '處理'), null, 15000)
  check(`${mode} nurse: an open alert with 處理`, hasHandle)
  const SECTION = 'section[aria-label=未處理通知]'
  const alertTitle = await p.evaluate((s) => {
    const b = [...document.querySelectorAll(`${s} button`)].find((x) => x.innerText.trim() === '處理')
    return b?.closest('li')?.querySelector('.font-bold')?.innerText.trim() ?? ''
  }, SECTION)
  await clickText(p, SECTION, '處理')
  await waitSel(p, `${SECTION} [data-handling-items]`)
  const items = await p.$$eval(`${SECTION} [data-handling-item]`, (bs) => bs.map((b) => b.dataset.handlingItem))
  check(`${mode} 18. nurse: exactly the four 處理項目`, JSON.stringify(items) === JSON.stringify(HANDLING), items.join(' | '))
  const fits = await p.$$eval(`${SECTION} [data-handling-item]`, (bs) => bs.every((b) => { const r = b.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && b.scrollWidth <= b.clientWidth + 1 }))
  check(`${mode} 390 px: 處理項目 not cut off`, fits && await noOverflow(p))
  await click(p, `${SECTION} [data-handling-item="${HANDLING[2]}"]`)
  const note1 = await p.$eval(`${SECTION} textarea`, (t) => t.value)
  const pressed = await p.$eval(`${SECTION} [data-handling-item="${HANDLING[2]}"]`, (b) => b.getAttribute('aria-pressed'))
  check(`${mode} 19. choosing a 處理項目 fills the note and marks it chosen`, note1 === HANDLING[2] && pressed === 'true', note1)
  await shot(p, SECTION, `home-${mode}-nurse-handling`)
  await clickText(p, SECTION, '取消')
  check(`${mode} 19. 取消 keeps the alert open (form closed)`, !(await p.$(`${SECTION} [data-handling-items]`))
    && await p.evaluate((s) => [...document.querySelectorAll(`${s} button`)].some((b) => b.innerText.trim() === '處理'), SECTION))
  await clickText(p, SECTION, '處理')
  await waitSel(p, `${SECTION} [data-handling-items]`)
  const kept = await p.$eval(`${SECTION} textarea`, (t) => t.value)
  check(`${mode} 19. 取消 keeps the draft (existing behaviour)`, kept === HANDLING[2], kept)
  await setValue(p, `${SECTION} textarea`, '')
  await click(p, `${SECTION} [data-handling-item="${HANDLING[1]}"]`)
  await click(p, `${SECTION} [data-handling-item="${HANDLING[3]}"]`)
  const note2 = await p.$eval(`${SECTION} textarea`, (t) => t.value)
  check(`${mode} 19. two 處理項目 combine`, note2 === `${HANDLING[1]}${HANDLING[3]}`, note2)
  await clickText(p, SECTION, '標示為已處理')
  await idle(p)
  await clickText(p, SECTION, '已處理')
  const resolvedOk = await waitFn(p, (args) => [...document.querySelectorAll(`${args.s} li`)].some((li) => li.innerText.includes(args.n)), { s: SECTION, n: note2 })
  check(`${mode} 19. 標示為已處理 → in 已處理 with the chosen items`, resolvedOk, alertTitle)
  await signOut(p)

  if (mode === 'API') {
    const mine = (await api('/notifications/me?per_page=50', pt)).body
    check(`${mode} 處理項目 text never reaches the patient`, mine && !JSON.stringify(mine).includes(HANDLING[1]) && !JSON.stringify(mine).includes(HANDLING[3]))
  }
  await ctx.close()
}

await browser.close()
check('no page / console errors', errors.length === 0, errors.slice(0, 5).join(' | '))
const passed = results.filter(Boolean).length
console.log(`\n${passed}/${results.length} checks passed`)
process.exit(passed === results.length ? 0 : 1)
