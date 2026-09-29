// Sprint 8 — manual / scheduled reminders, in mock mode and API mode: nurse writes a reminder
// now and one for a set time (patient detail 提醒) → patient sees only the due one (390 px,
// unread badge) → the scheduled one appears once due → same handling lifecycle as risk alerts
// (接手 → 開始處理 → 完成, internal note never shown to the patient) → validation → permissions →
// audit → mock / API response structure.
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

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const tokenFor = async (email) => (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password: 'Demo@1234' }) })).body.data.access_token
const text = (p) => p.evaluate(() => document.body.innerText)
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
/** datetime-local value in Asia/Taipei, `minutes` from now. */
const localIn = (minutes) => new Date(Date.now() + 8 * 3600e3 + minutes * 60e3).toISOString().slice(0, 16)
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
  const NOW_TITLE = `回診前請空腹 ${mode}`
  const LATER_TITLE = `明天化療提醒 ${mode}`
  const SOON_TITLE = `一分鐘後的提醒 ${mode}`
  const NOTE = `已電話確認病人了解 ${mode}（內部）`
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(desk)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })

  let pid = MOCK_P1
  let nt
  if (mode === 'API') {
    nt = await tokenFor('nurse01@demo.local')
    pid = (await api('/patients?q=P00001', nt)).body.data[0].id
  }

  // ---------------------------------------------------------------- nurse: write reminders
  await signIn(p, 'nurse01@demo.local')
  await go(p, `/nurse/patients/${pid}`)
  check(`${mode} nurse: 提醒 panel on the patient page`, await waitSel(p, '[data-reminders] [data-new-reminder]') && await waitSel(p, '[data-sent-list], [data-reminders] p'))
  // a reminder due in ~65 s, created through the handler / API (checked at the end, once due)
  const soonAt = new Date(Date.now() + 65e3).toISOString()
  if (mode === 'API') {
    await api('/notifications', nt, { method: 'POST', body: JSON.stringify({ patient_id: pid, title: SOON_TITLE, message: '這則提醒一分鐘後才會出現', scheduled_for: soonAt }) })
  } else {
    await p.evaluate(async (a) => (await import('/src/mock/api.js')).mockCreateReminder({ patient_id: a.pid, title: a.t, message: '這則提醒一分鐘後才會出現', scheduled_for: a.at }), { pid, t: SOON_TITLE, at: soonAt })
  }
  const soonCreatedAt = Date.now()

  await click(p, '[data-new-reminder]')
  await click(p, '[data-reminder-form] button[type=submit]')
  check(`${mode} nurse: empty form → field errors`, await waitText(p, '請輸入標題') && (await text(p)).includes('請輸入提醒內容'))
  await setValue(p, '[data-reminder-form] input[name=title]', NOW_TITLE)
  await setValue(p, '[data-reminder-form] textarea[name=message]', '明天 08:30 報到，05:00 起請空腹。')
  await click(p, '[data-reminder-form] button[type=submit]')
  check(`${mode} nurse: reminder sent now → listed as 待處理`, await waitText(p, '已送出，病人可在「通知」看到')
    && await waitFn(p, (t) => [...document.querySelectorAll('[data-sent-list] [data-reminder]')].some((li) => li.innerText.includes(t) && li.dataset.status === 'new'), NOW_TITLE))

  await click(p, '[data-new-reminder]')
  await setValue(p, '[data-reminder-form] input[name=title]', LATER_TITLE)
  await setValue(p, '[data-reminder-form] textarea[name=message]', '記得帶健保卡與藥袋。')
  await p.$eval('[data-reminder-form] input[name=timing][value=later]', (r) => r.click())
  await p.waitForSelector('[data-reminder-form] input[name=scheduled_for]')
  await setValue(p, '[data-reminder-form] input[name=scheduled_for]', localIn(-30))
  await click(p, '[data-reminder-form] button[type=submit]')
  check(`${mode} nurse: a time in the past is rejected`, await waitText(p, '請選擇 1 分鐘後到一年內的時間'))
  await setValue(p, '[data-reminder-form] input[name=scheduled_for]', localIn(24 * 60))
  await click(p, '[data-reminder-form] button[type=submit]')
  check(`${mode} nurse: scheduled reminder → 排程中 (patient cannot see it yet)`, await waitText(p, '已排定，將於')
    && await waitFn(p, (t) => document.querySelector('[data-scheduled-list]')?.innerText.includes(t), LATER_TITLE)
    && !(await p.$eval('[data-sent-list]', (e) => e.innerText)).includes(LATER_TITLE))
  await signOut(p)

  // ---------------------------------------------------------------- patient (390 px)
  await p.setViewport(phone)
  await signIn(p, 'patient01@demo.local')
  await go(p, '/patient/notifications')
  await waitSel(p, '[data-patient-notifications] [data-notification]')
  let ptext = await text(p)
  check(`${mode} patient: the sent reminder is in 通知 (unread)`, ptext.includes(NOW_TITLE) && await p.evaluate((t) =>
    [...document.querySelectorAll('[data-notification]')].some((li) => li.innerText.includes(t) && li.dataset.read === 'false'), NOW_TITLE))
  check(`${mode} patient: the scheduled reminders are not shown yet`, !ptext.includes(LATER_TITLE) && !ptext.includes(SOON_TITLE))
  check(`${mode} 390px: 通知 with reminders has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-patient-reminders.png`, fullPage: true })
  const rid = await p.evaluate((t) => [...document.querySelectorAll('[data-notification]')].find((li) => li.innerText.includes(t)).dataset.notification, NOW_TITLE)
  await p.$eval(`[data-notification="${rid}"] button`, (b) => b.click())
  check(`${mode} patient: opening the reminder marks it read`, await waitFn(p, (id) => document.querySelector(`[data-notification="${id}"]`)?.dataset.read === 'true', rid))
  if (mode === 'API') {
    const pt = await tokenFor('patient01@demo.local')
    shapes.API.patientList = shape((await api('/notifications?per_page=50', pt)).body)
    const r = await api('/notifications', pt, { method: 'POST', body: JSON.stringify({ patient_id: 'me', title: 'x', message: 'y' }) })
    const s = await api('/notifications/scheduled?patient_id=me', pt)
    const a = await api(`/notifications/${rid}/acknowledge`, pt, { method: 'POST' })
    check('API patient: cannot create, list scheduled or handle reminders → 403', r.status === 403 && s.status === 403 && a.status === 403, `${r.status} ${s.status} ${a.status}`)
  } else {
    const m = await p.evaluate(async () => {
      const mock = await import('/src/mock/api.js')
      const res = { patientList: mock.mockMyNotifications() }
      for (const [k, fn] of [['create', () => mock.mockCreateReminder({ patient_id: 'me', title: 'x', message: 'y' })], ['scheduled', () => mock.mockScheduledReminders('me')]]) {
        try { fn(); res[k] = 200 } catch (e) { res[k] = e.status }
      }
      return res
    })
    shapes.MOCK.patientList = shape(m.patientList)
    check('MOCK patient: cannot create or list scheduled reminders → 403', m.create === 403 && m.scheduled === 403, `${m.create} ${m.scheduled}`)
  }
  await p.$eval('[data-logout], a[href="/patient/me"]', (e) => e.click()).catch(() => {})
  await go(p, '/patient/me')
  await waitSel(p, '[data-logout]')
  await click(p, '[data-logout]')
  await waitFn(p, () => location.pathname.startsWith('/login'))
  await p.setViewport(desk)

  // ---------------------------------------------------------------- nurse: lifecycle
  await signIn(p, 'nurse01@demo.local')
  await go(p, `/nurse/patients/${pid}`)
  await waitSel(p, `[data-reminder="${rid}"]`)
  await click(p, `[data-reminder="${rid}"] [data-step="acknowledge"]`)
  check(`${mode} lifecycle: 接手 → 已接手`, await waitSel(p, `[data-reminder="${rid}"][data-status="acknowledged"]`))
  await click(p, `[data-reminder="${rid}"] [data-step="start"]`)
  check(`${mode} lifecycle: 開始處理 → 處理中`, await waitSel(p, `[data-reminder="${rid}"][data-status="in_progress"]`))
  await click(p, `[data-reminder="${rid}"] [data-step="resolve"]`)
  await p.waitForSelector(`[data-reminder="${rid}"] [data-resolve-form]`)
  await click(p, `[data-reminder="${rid}"] [data-resolve-form] button[type=submit]`)
  check(`${mode} lifecycle: 完成 needs a note`, await waitText(p, '請填寫處理說明'))
  await setValue(p, `[data-reminder="${rid}"] input[name=resolution_note]`, NOTE)
  await click(p, `[data-reminder="${rid}"] [data-resolve-form] button[type=submit]`)
  check(`${mode} lifecycle: 完成 → 已完成 with the internal note`, await waitSel(p, `[data-reminder="${rid}"][data-status="resolved"]`) && await waitText(p, NOTE))
  check(`${mode} lifecycle: no further step after 已完成`, !(await p.$(`[data-reminder="${rid}"] [data-step]`)))

  // ---------------------------------------------------------------- shapes (staff)
  if (mode === 'API') {
    const created = await api('/notifications', nt, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ patient_id: pid, title: '結構比對', message: 'x' }) })
    shapes.API.create = shape(created.body)
    shapes.API.list = shape((await api(`/notifications?type=reminder&patient_id=${pid}&per_page=50`, nt)).body)
    shapes.API.scheduled = shape((await api(`/notifications/scheduled?patient_id=${pid}`, nt)).body)
    shapes.API.detail = shape((await api(`/notifications/${rid}`, nt)).body)
    // validation / permissions / audit
    const bad = await api('/notifications', nt, { method: 'POST', body: JSON.stringify({ patient_id: pid, title: '', message: '', severity: 'critical' }) })
    check('API validation: title, message, severity (no critical) → 400', bad.status === 400 && ['title', 'message', 'severity'].every((f) => bad.body.error.details.some((d) => d.field === f)))
    const at = await tokenFor('admin01@demo.local')
    const other = (await api('/patients', at, { method: 'POST', body: JSON.stringify({ display_name: '測試病人 提醒', gender: 'male', date_of_birth: '1966-06-06' }) })).body.data
    const r404 = await api('/notifications', nt, { method: 'POST', body: JSON.stringify({ patient_id: other.id, title: 'x', message: 'y' }) })
    check('API nurse: reminder for an unassigned patient → 404', r404.status === 404)
    const audit = (await api('/admin/audit-logs?resource_type=notifications&action=CREATE&per_page=50', at)).body.data
    check('API audit: reminder creation audited with origin and actor', audit.some((a) => a.changes?.origin === 'scheduled') && audit.some((a) => a.changes?.origin === 'manual')
      && audit.every((a) => a.actor?.role === 'nurse' || a.actor?.role === 'admin'))
    const steps = (await api(`/admin/audit-logs?resource_type=notifications&per_page=200`, at)).body.data.filter((a) => a.resource_id === String(rid))
    check('API audit: lifecycle steps audited', steps.length >= 3, `${steps.length}`)
  } else {
    const m = await p.evaluate(async (a) => {
      const mock = await import('/src/mock/api.js')
      return {
        create: mock.mockCreateReminder({ patient_id: a.pid, title: '結構比對', message: 'x' }, crypto.randomUUID()),
        list: mock.mockListReminders(a.pid, { status: 'all' }), scheduled: mock.mockScheduledReminders(a.pid),
        detail: { data: mock.mockGetNotification(Number(a.rid)) },
        bad: (() => { try { mock.mockCreateReminder({ patient_id: a.pid, title: '', message: '', severity: 'critical' }) } catch (e) { return { status: e.status, fields: e.details.map((d) => d.field) } } })(),
      }
    }, { pid, rid })
    for (const k of ['create', 'list', 'scheduled', 'detail']) shapes.MOCK[k] = shape(m[k])
    check('MOCK validation: title, message, severity (no critical) → 400', m.bad.status === 400 && ['title', 'message', 'severity'].every((f) => m.bad.fields.includes(f)))
  }
  await signOut(p)

  // ---------------------------------------------------------------- the ~1 minute reminder becomes due
  const wait = soonCreatedAt + 67e3 - Date.now()
  if (wait > 0) await new Promise((r) => setTimeout(r, wait))
  await p.setViewport(phone)
  await signIn(p, 'patient01@demo.local')
  await go(p, '/patient/notifications')
  check(`${mode} patient: the scheduled reminder appears once due`, await waitText(p, SOON_TITLE) && !(await text(p)).includes(LATER_TITLE))
  check(`${mode} patient: the internal note and nurse names never show`, !(await text(p)).includes(NOTE) && !(await text(p)).includes('測試護理師'))
  await ctx.close()
}

for (const k of ['create', 'list', 'scheduled', 'detail', 'patientList']) {
  const diff = shapeDiff(shapes.API[k], shapes.MOCK[k])
  check(`mock / API ${k}: same response structure`, shapes.API[k] && shapes.MOCK[k] && diff.length === 0, diff.join('; '))
}
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
