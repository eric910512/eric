// Sprint 3 — treatment schedule, in mock mode and API mode: nurse creates appointments with
// preparation steps → nurse today list → check-in → patient sees 今日行程 (with status and the
// yellow preparation reminder) and 接下來的行程 → complete → reschedule keeps the history →
// timeline APPOINTMENT events (no internal notes for the patient) → plan with infusion
// appointments → cycle delay moves them → 390 px → mock / API response structure.
import puppeteer from 'puppeteer-core'
import { API_URL, CHROME, CHROME_ARGS, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const idle = (p) => p.waitForNetworkIdle({ idleTime: 400, timeout: 8000 }).catch(() => {})
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })
const errors = []
const desk = { width: 1440, height: 1400, deviceScaleFactor: 1 }
const phone = { width: 390, height: 1200, deviceScaleFactor: 1 }
const tpe = (ms) => new Date(ms + 8 * 3600000).toISOString().slice(0, 16) // datetime-local value in Asia/Taipei
const tpeDay = (ms) => new Date(ms + 8 * 3600000).toISOString().slice(0, 10)

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const tokenFor = async (email, password = 'Demo@1234') => (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password }) })).body.data.access_token
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
async function signIn(p, email, password = 'Demo@1234') {
  if (!(await path(p)).startsWith('/login')) await go(p, '/login')
  await p.waitForSelector('input[name=email]')
  await setValue(p, 'input[name=email]', email)
  await setValue(p, 'input[name=password]', password)
  await p.click('button[type=submit]')
  await idle(p)
}
async function signOut(p) {
  await p.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.innerText.trim() === '登出').click())
  await p.waitForFunction(() => location.pathname === '/login')
}
const mockCall = (p, name, args) => p.evaluate(async (name, args) => {
  const m = await import('/src/mock/api.js')
  try { return m[name](...args) } catch (e) { return { error: { status: e.status, code: e.code, message: e.message } } }
}, name, args)
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
const upcomingRows = (p) => p.$$eval('[data-upcoming] [data-appointment]', (els) => els.map((e) => ({ id: e.dataset.appointment, status: e.dataset.appointmentStatus, text: e.innerText })))

async function newAppointment(p, { type, title, at, step = null, notes = '' }) {
  await clickIn(p, '[data-new-appointment]')
  await p.waitForSelector('[data-appointment-form]')
  await p.select('[data-appointment-form] select[name=appointment_type]', type)
  await setValue(p, '[data-appointment-form] input[name=title]', title)
  await setValue(p, '[data-appointment-form] input[name=scheduled_at]', at)
  await setValue(p, '[data-appointment-form] input[name=location]', '一樓檢驗科')
  if (notes) await setValue(p, '[data-appointment-form] input[name=notes]', notes)
  if (step) {
    await clickIn(p, '[data-add-step]')
    await setValue(p, '[data-appointment-form] [data-step] input[name=step_text]', step)
  }
  await clickIn(p, '[data-appointment-form] button[type=submit]')
  return waitText(p, '已建立行程')
}

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(desk)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })
  const name = `行程測試 ${mode}`
  const email = `appt.${mode.toLowerCase()}@demo.local`

  // ---------------------------------------------------------------- setup (admin): patient with account, assigned to nurse01
  await signIn(p, 'admin01@demo.local')
  let pid, temp
  const newPatient = { display_name: name, gender: 'male', date_of_birth: '1968-04-04', account: { email } }
  if (mode === 'API') {
    const at = await tokenFor('admin01@demo.local')
    const created = (await api('/patients', at, { method: 'POST', body: JSON.stringify(newPatient) })).body.data
    pid = created.id
    temp = created.account.temporary_password
    const nurse = (await api('/admin/users?role=nurse', at)).body.data.find((n) => n.email === 'nurse01@demo.local')
    await api(`/patients/${pid}/nurse-assignments`, at, { method: 'POST', body: JSON.stringify({ nurse_id: nurse.id, is_primary: true }) })
  } else {
    const created = (await mockCall(p, 'mockCreatePatient', [newPatient])).data
    pid = created.id
    temp = created.account.temporary_password
    await mockCall(p, 'mockCreateAssignment', [pid, { nurse_id: 'mock-user-nurse01', is_primary: true }])
  }
  await signOut(p)

  // ---------------------------------------------------------------- nurse: two appointments
  await signIn(p, 'nurse01@demo.local')
  await go(p, `/nurse/patients/${pid}`)
  check(`${mode} nurse: patient page has 治療行程 (empty)`, await waitSel(p, '[data-appointments-panel]') && await waitText(p, '目前沒有行程'))
  const now = Date.now()
  check(`${mode} nurse: create a lab draw earlier today with a preparation step`,
    await newAppointment(p, { type: 'lab_draw', title: '化療前抽血', at: tpe(now - 60000), step: '提前 10 分鐘報到', notes: '內部備註：血管不好找' }))
  check(`${mode} nurse: create a clinic visit tomorrow`, await newAppointment(p, { type: 'clinic_visit', title: '門診追蹤', at: tpe(now + 86400000) }))
  let rows = await upcomingRows(p)
  const lab = rows.find((r) => r.text.includes('化療前抽血'))
  const visit = rows.find((r) => r.text.includes('門診追蹤'))
  check(`${mode} nurse: both listed soonest first with the step`, rows.length === 2 && rows[0].id === lab?.id && lab.text.includes('提前 10 分鐘報到'), JSON.stringify(rows.map((r) => r.text.slice(0, 30))))
  check(`${mode} nurse: tomorrow's visit cannot be checked in yet`, !(await p.$(`[data-appointment="${visit.id}"] [data-check-in]`)))

  await go(p, '/nurse')
  check(`${mode} nurse dashboard: 今日行程 lists the lab draw with the patient`, await waitText(p, `${name}（`) && (await text(p)).includes('化療前抽血'))
  await go(p, `/nurse/patients/${pid}`)
  await p.waitForSelector(`[data-appointment="${lab.id}"] [data-check-in]`)
  await clickIn(p, `[data-appointment="${lab.id}"] [data-check-in]`)
  check(`${mode} nurse: check-in → 已報到`, await p.waitForSelector(`[data-upcoming] [data-appointment="${lab.id}"][data-appointment-status=checked_in]`, { timeout: 8000 }).then(() => true, () => false))
  await signOut(p)

  // ---------------------------------------------------------------- patient: first sign-in, 今日行程 + 接下來的行程
  await signIn(p, email, temp)
  await p.waitForFunction(() => location.pathname === '/change-password')
  await setValue(p, 'input[name=current_password]', temp)
  await setValue(p, 'input[name=new_password]', 'Appt2026ok')
  await setValue(p, 'input[name=confirm_password]', 'Appt2026ok')
  await p.click('[data-change-password] button[type=submit]')
  await p.waitForFunction(() => location.pathname === '/patient')
  check(`${mode} patient: 今日行程 shows the lab draw, 已報到, and the yellow reminder`, await waitText(p, '化療前抽血')
    && (await text(p)).includes('提前 10 分鐘報到') && (await p.$eval('[data-appointment-status]', (e) => e.innerText).catch(() => '')) === '已報到')
  check(`${mode} patient: no internal notes on the home page`, !(await text(p)).includes('內部備註'))
  await go(p, '/patient/treatment')
  check(`${mode} patient: 接下來的行程 lists tomorrow's visit (no plan needed)`, await waitSel(p, '[data-my-upcoming]') && (await p.$eval('[data-my-upcoming]', (e) => e.innerText)).includes('門診追蹤'))
  await p.setViewport(phone)
  await idle(p)
  check(`${mode} 390px: 我的療程 with upcoming appointments has no horizontal overflow`, await noOverflow(p))
  await p.setViewport(desk)
  await signOut(p)

  // ---------------------------------------------------------------- nurse: complete, reschedule
  await signIn(p, 'nurse01@demo.local')
  await go(p, `/nurse/patients/${pid}`)
  await p.waitForSelector(`[data-appointment="${lab.id}"] [data-complete]`)
  await clickIn(p, `[data-appointment="${lab.id}"] [data-complete]`)
  await waitText(p, '化療前抽血：已完成')
  await clickIn(p, `[data-appointment="${visit.id}"] [data-reschedule]`)
  await p.waitForSelector('[data-appointment-action] input[name=new_scheduled_at]')
  await setValue(p, '[data-appointment-action] input[name=new_scheduled_at]', tpe(now + 3 * 86400000))
  await setValue(p, '[data-appointment-action] input[name=reason]', '病人請假')
  await clickIn(p, '[data-appointment-action] button[type=submit]')
  await waitText(p, '已改期，原行程保留在紀錄中')
  rows = await upcomingRows(p)
  const hist = await p.$$eval('[data-appointment-history] [data-appointment]', (els) => els.map((e) => ({ id: e.dataset.appointment, status: e.dataset.appointmentStatus, text: e.innerText })))
  check(`${mode} nurse: reschedule → new appointment upcoming (改期而來), original kept as 已改期`, rows.length === 1 && rows[0].text.includes('改期而來')
    && rows[0].text.includes(tpeDay(now + 3 * 86400000)) && hist.some((h) => h.id === visit.id && h.status === 'rescheduled')
    && hist.some((h) => h.id === lab.id && h.status === 'completed'), JSON.stringify(hist))
  await go(p, `/nurse/${pid}`)
  await waitText(p, '病人照護時間軸')
  await idle(p)
  const stl = await p.$eval('section[data-timeline]', (e) => e.innerText).catch(() => '')
  check(`${mode} staff timeline: the lab draw as an appointment event (已完成)`, stl.includes('化療前抽血') && stl.includes('已完成，一樓檢驗科'), stl.slice(0, 200).replace(/\s+/g, ' '))

  // ---------------------------------------------------------------- chemotherapy: infusion appointments, delay moves them
  const dxBody = { cancer_type_code: 'C11', diagnosis_date: '2026-08-01' }
  if (mode === 'API') await api(`/patients/${pid}/diagnoses`, await tokenFor('nurse01@demo.local'), { method: 'POST', body: JSON.stringify(dxBody) })
  else await mockCall(p, 'mockCreateDiagnosis', [pid, dxBody])
  await go(p, `/nurse/patients/${pid}`)
  await p.waitForSelector('[data-new-plan]')
  await clickIn(p, '[data-new-plan]')
  await p.waitForSelector('[data-plan-form] select[name=regimen_id] option[value]:not([value=""])')
  const regimen = await p.$$eval('[data-plan-form] select[name=regimen_id] option', (os) => os.find((o) => o.innerText.startsWith('Cisplatin q3w'))?.value)
  await p.select('[data-plan-form] select[name=regimen_id]', regimen)
  await setValue(p, '[data-plan-form] input[name=start_date]', tpeDay(now + 2 * 86400000))
  await setValue(p, '[data-plan-form] input[name=infusion_time]', '09:30')
  await clickIn(p, '[data-plan-form] button[type=submit]')
  await waitSel(p, '[data-current-plan]')
  await idle(p)
  rows = await upcomingRows(p)
  const infusions = rows.filter((r) => r.text.includes('化療注射'))
  check(`${mode} nurse: plan created 3 infusion appointments at 09:30`, infusions.length === 3 && infusions[0].text.includes(`${tpeDay(now + 2 * 86400000)} 09:30`) && infusions[0].text.includes('第 1 次 Cycle'),
    JSON.stringify(infusions.map((r) => r.text.slice(0, 40))))
  await clickIn(p, '[data-cycle="2"] [data-delay-cycle]')
  await p.waitForSelector('[data-cycle-form] input[name=new_scheduled_date]')
  await setValue(p, '[data-cycle-form] input[name=new_scheduled_date]', tpeDay(now + 26 * 86400000))
  await setValue(p, '[data-cycle-form] input[name=delay_reason]', 'ANC 過低')
  await clickIn(p, '[data-cycle-form] button[type=submit]')
  await waitText(p, '已延後 Cycle')
  await idle(p)
  await sleep(300)
  rows = await upcomingRows(p)
  check(`${mode} nurse: delaying cycle 2 moved its infusion by 3 days (old one kept as 已改期)`,
    rows.some((r) => r.text.includes('第 2 次化療注射') && r.text.includes(`${tpeDay(now + 26 * 86400000)} 09:30`) && r.text.includes('改期而來'))
    && (await p.$$eval('[data-appointment-history] [data-appointment]', (els) => els.filter((e) => e.textContent.includes('第 2 次化療注射') && e.dataset.appointmentStatus === 'rescheduled').length)) === 1,
    JSON.stringify(rows.map((r) => r.text.replace(/\s+/g, ' ').slice(0, 60))) + ' | ' + (await p.$eval('[data-chemo-panel]', (e) => e.innerText.replace(/\s+/g, ' ').slice(0, 300))))
  await p.setViewport(phone)
  await go(p, `/nurse/patients/${pid}`)
  await p.waitForSelector('[data-upcoming]')
  check(`${mode} 390px: appointments panel has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-appointments.png`, fullPage: true })
  await p.setViewport(desk)

  // ---------------------------------------------------------------- shapes
  if (mode === 'API') {
    const t = await tokenFor('nurse01@demo.local')
    shapes.API.list = shape((await api(`/chemotherapy/appointments?patient_id=${pid}`, t)).body)
    shapes.API.today = shape((await api('/dashboard/widgets/today-appointments/data', t)).body)
    shapes.API.plans = shape((await api(`/chemotherapy/plans?patient_id=${pid}`, t)).body)
  } else {
    const m = await p.evaluate(async (pid) => {
      const mock = await import('/src/mock/api.js')
      return { list: mock.mockListAppointments(pid), today: mock.mockTodayAppointments(), plans: mock.mockListPlans(pid) }
    }, pid)
    for (const [k, v] of Object.entries(m)) shapes.MOCK[k] = shape(v)
  }
  await signOut(p)

  // ---------------------------------------------------------------- patient timeline: no internal notes
  await signIn(p, email, 'Appt2026ok')
  await p.waitForFunction(() => location.pathname === '/patient')
  await go(p, '/patient/timeline')
  await waitSel(p, 'section[data-timeline] li[data-event-type]')
  const ptl = await p.$eval('section[data-timeline]', (e) => e.innerText)
  check(`${mode} patient timeline: appointment shown, internal notes hidden`, ptl.includes('化療前抽血') && !ptl.includes('內部備註'))
  await ctx.close()
}

for (const k of ['list', 'today', 'plans']) {
  const diff = shapeDiff(shapes.API[k], shapes.MOCK[k])
  check(`mock / API ${k}: same response structure`, shapes.API[k] && shapes.MOCK[k] && diff.length === 0, diff.join('; '))
}
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
