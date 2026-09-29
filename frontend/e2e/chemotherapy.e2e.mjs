// Sprint 2 — chemotherapy plans, cycles and medication records, in mock mode and API mode:
// new patient (set up through the API / mock handlers) → nurse creates a plan in the UI → starts
// Cycle 1 → records an administration (double submit → one record) → dashboard / caseload /
// staff timeline show it → correction keeps the original → mark-error → patient sees own plan
// without staff details → 390 px → mock / API response structure.
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
/** Run a mock handler inside the page (mock mode: the signed-in user is the viewer). */
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
const medRows = (p) => p.$$eval('[data-medication-history] [data-medication]', (els) => els.map((e) => ({ id: e.dataset.medication, status: e.dataset.recordStatus, text: e.innerText })))

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(desk)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })
  const name = `化療測試 ${mode}`

  // ---------------------------------------------------------------- setup: patient assigned to nurse01, with a diagnosis
  let pid
  await signIn(p, 'admin01@demo.local')
  if (mode === 'API') {
    const at = await tokenFor('admin01@demo.local')
    pid = (await api('/patients', at, { method: 'POST', body: JSON.stringify({ display_name: name, gender: 'female', date_of_birth: '1971-02-03' }) })).body.data.id
    const nurse = (await api('/admin/users?role=nurse', at)).body.data.find((n) => n.email === 'nurse01@demo.local')
    await api(`/patients/${pid}/nurse-assignments`, at, { method: 'POST', body: JSON.stringify({ nurse_id: nurse.id, is_primary: true }) })
  } else {
    pid = (await mockCall(p, 'mockCreatePatient', [{ display_name: name, gender: 'female', date_of_birth: '1971-02-03' }])).data.id
    await mockCall(p, 'mockCreateAssignment', [pid, { nurse_id: 'mock-user-nurse01', is_primary: true }])
  }
  await signOut(p)
  await signIn(p, 'nurse01@demo.local')
  const dxBody = { cancer_type_code: 'C11', diagnosis_date: '2026-08-01', stage: 'II' }
  if (mode === 'API') await api(`/patients/${pid}/diagnoses`, await tokenFor('nurse01@demo.local'), { method: 'POST', body: JSON.stringify(dxBody) })
  else await mockCall(p, 'mockCreateDiagnosis', [pid, dxBody])

  // ---------------------------------------------------------------- nurse: plan → start cycle 1
  await go(p, `/nurse/patients/${pid}`)
  check(`${mode} nurse: patient page has the chemotherapy panel (no plan yet)`, await waitSel(p, '[data-chemo-panel]') && await waitText(p, '尚未建立化療療程'))
  await p.click('[data-new-plan]')
  await p.waitForSelector('[data-plan-form] select[name=regimen_id] option[value]:not([value=""])')
  const regimen = await p.$$eval('[data-plan-form] select[name=regimen_id] option', (os) => os.find((o) => o.innerText.startsWith('Cisplatin q3w'))?.value)
  await p.select('[data-plan-form] select[name=regimen_id]', regimen)
  await setValue(p, '[data-plan-form] input[name=plan_name]', `Cisplatin 療程 ${mode}`)
  await setValue(p, '[data-plan-form] input[name=attending_physician_name]', '測試醫師 E2E')
  await p.click('[data-plan-form] button[type=submit]')
  check(`${mode} nurse: plan created with 3 scheduled cycles`, await waitSel(p, '[data-current-plan][data-plan-status=planned]')
    && (await p.$$('[data-cycles] [data-cycle-status=scheduled]')).length === 3)
  await p.click('[data-cycle="1"] [data-start-cycle]')
  await p.waitForSelector('[data-cycle-form]')
  check(`${mode} nurse: start form explains that earlier records are not changed`, (await text(p)).includes('之前的紀錄不會改變'))
  await p.click('[data-cycle-form] button[type=submit]')
  check(`${mode} nurse: cycle 1 in progress, day 1; plan active`, await waitSel(p, '[data-cycle="1"][data-cycle-status=in_progress]')
    && (await p.$eval('[data-cycle="1"]', (e) => e.innerText)).includes('第 1 天') && !!(await p.$('[data-current-plan][data-plan-status=active]')))

  // ---------------------------------------------------------------- medication: double submit → one record
  await p.click('[data-cycle="1"] [data-record-medication]')
  await p.waitForSelector('[data-medication-form] select[name=drug_id] option')
  const cis = await p.$$eval('[data-medication-form] select[name=drug_id] option', (os) => os.find((o) => o.innerText === 'Cisplatin').value)
  await p.select('[data-medication-form] select[name=drug_id]', cis)
  await setValue(p, '[data-medication-form] input[name=dose_value]', '170')
  await setValue(p, '[data-medication-form] input[name=infusion_duration_min]', '120')
  await p.evaluate(() => { const b = document.querySelector('[data-medication-form] button[type=submit]'); b.click(); b.click() })
  await waitSel(p, '[data-medication-history]')
  await idle(p)
  await sleep(400)
  let rows = await medRows(p)
  check(`${mode} nurse: medication recorded once despite a double submit`, rows.length === 1 && rows[0].text.includes('Cisplatin 170 mg') && rows[0].status === 'final', JSON.stringify(rows))
  check(`${mode} nurse: history shows cycle day and who gave it`, rows[0]?.text.includes('第 1 次 Cycle 第 1 天') && rows[0]?.text.includes('測試護理師 林'))

  // ---------------------------------------------------------------- dashboard / caseload / timeline
  await go(p, `/nurse/${pid}`)
  check(`${mode} nurse dashboard: treatment progress shows cycle 1 day 1`, await waitText(p, '第 1 次療程，第 1 天'))
  check(`${mode} nurse dashboard: caseload shows the cycle`, await p.waitForFunction((n) => [...document.querySelectorAll('button')].some((b) => b.innerText.includes(n) && b.innerText.includes('第 1 次療程第 1 天')), { timeout: 8000 }, name).then(() => true, () => false))
  check(`${mode} staff timeline: cycle start and the administration`, await waitText(p, '第 1 次化療開始') && await waitText(p, 'Cisplatin 170 mg IV（已給藥）'),
    (await p.$eval('section[data-timeline]', (e) => e.innerText).catch((e) => e.message)).slice(0, 300).replace(/\s+/g, ' '))

  // ---------------------------------------------------------------- correction + mark error
  await go(p, `/nurse/patients/${pid}`)
  await p.waitForSelector('[data-medication-history] [data-amend]')
  await p.click('[data-medication-history] [data-amend]')
  await p.waitForSelector('[data-medication-form] input[name=amend_reason]')
  await p.click('[data-medication-form] button[type=submit]')
  await sleep(400)
  check(`${mode} nurse: correction without a reason is rejected`, (await text(p)).includes('請填寫更正原因'))
  await setValue(p, '[data-medication-form] input[name=dose_value]', '160')
  await setValue(p, '[data-medication-form] input[name=amend_reason]', '劑量誤植')
  await p.click('[data-medication-form] button[type=submit]')
  await waitText(p, '已更正，原紀錄保留在歷史中')
  rows = await medRows(p)
  check(`${mode} nurse: correction adds a record, original kept as 已更正`, rows.length === 2
    && rows.some((r) => r.status === 'final' && r.text.includes('160 mg') && r.text.includes('更正紀錄'))
    && rows.some((r) => r.status === 'amended' && r.text.includes('170 mg') && r.text.includes('已由新紀錄更正')), JSON.stringify(rows.map((r) => r.status)))
  await p.click('[data-cycle="1"] [data-record-medication]')
  await p.waitForSelector('[data-medication-form] select[name=drug_id] option')
  await setValue(p, '[data-medication-form] input[name=dose_value]', '8')
  await p.select('[data-medication-form] select[name=medication_type]', 'premedication')
  await p.click('[data-medication-form] button[type=submit]')
  await waitText(p, '已登錄給藥')
  const premed = (await medRows(p)).find((r) => r.text.includes('8 mg'))
  await p.click(`[data-medication="${premed.id}"] [data-mark-error]`)
  await setValue(p, `[data-medication="${premed.id}"] input[name=error_reason]`, '登錄錯誤')
  await p.click(`[data-medication="${premed.id}"] form button[type=submit]`)
  await waitText(p, '已標示為錯誤')
  check(`${mode} nurse: mark error keeps the record as 標示錯誤`, (await medRows(p)).find((r) => r.id === premed.id)?.status === 'entered_in_error')
  await go(p, `/nurse/${pid}`)
  await waitText(p, '病人照護時間軸')
  await idle(p)
  const tl = await p.$eval('section[data-timeline]', (e) => e.innerText).catch(() => '')
  check(`${mode} staff timeline: corrected dose only; erroneous record hidden`, tl.includes('Cisplatin 160 mg IV') && !tl.includes('Cisplatin 170 mg') && !tl.includes('8 mg'))

  // ---------------------------------------------------------------- 390 px (nurse chemo panel)
  await p.setViewport(phone)
  await go(p, `/nurse/patients/${pid}`)
  await p.waitForSelector('[data-medication-history]')
  check(`${mode} 390px: chemotherapy panel has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-chemo-panel.png`, fullPage: true })
  await p.setViewport(desk)

  // ---------------------------------------------------------------- shapes (staff)
  if (mode === 'API') {
    const t = await tokenFor('nurse01@demo.local')
    shapes.API.plans = shape((await api(`/chemotherapy/plans?patient_id=${pid}`, t)).body)
    shapes.API.medications = shape((await api(`/chemotherapy/medications?patient_id=${pid}`, t)).body)
    shapes.API.drugs = shape((await api('/chemotherapy/drugs', t)).body)
    shapes.API.regimens = shape((await api('/chemotherapy/regimens', t)).body)
    const planId = (await api(`/chemotherapy/plans?patient_id=${pid}`, t)).body.data[0].id
    const cyc = (await api(`/chemotherapy/plans?patient_id=${pid}`, t)).body.data[0].cycles[0].id
    const b = { drug_id: 1, medication_type: 'chemo', dose_value: 1, dose_unit: 'mg', administration_status: 'given', administered_at: new Date(Date.now() - 60000).toISOString() }
    const key = crypto.randomUUID()
    const r1 = await api(`/chemotherapy/cycles/${cyc}/medications`, t, { method: 'POST', body: JSON.stringify(b), headers: { 'Idempotency-Key': key } })
    const r2 = await api(`/chemotherapy/cycles/${cyc}/medications`, t, { method: 'POST', body: JSON.stringify(b), headers: { 'Idempotency-Key': key } })
    check('API: same Idempotency-Key twice → same record', r1.status === 201 && r2.body.data.id === r1.body.data.id)
    shapes.API.error404 = (await api(`/chemotherapy/plans/${planId}`, await tokenFor('patient01@demo.local'))).status
  } else {
    const m = await p.evaluate(async (pid) => {
      const mock = await import('/src/mock/api.js')
      return { plans: mock.mockListPlans(pid), medications: mock.mockListPatientMedications(pid), drugs: mock.mockListDrugs(), regimens: mock.mockListRegimens() }
    }, pid)
    for (const [k, v] of Object.entries(m)) shapes.MOCK[k] = shape(v)
    const planId = m.plans.data[0].id
    const cyc = m.plans.data[0].cycles[0].id
    const b = { drug_id: 1, medication_type: 'chemo', dose_value: 1, dose_unit: 'mg', administration_status: 'given', administered_at: new Date(Date.now() - 60000).toISOString() }
    const r1 = await mockCall(p, 'mockCreateMedication', [cyc, b, 'k1'])
    const r2 = await mockCall(p, 'mockCreateMedication', [cyc, b, 'k1'])
    check('MOCK: same Idempotency-Key twice → same record', r1.data?.id && r2.data?.id === r1.data.id)
    await signOut(p)
    await signIn(p, 'patient01@demo.local')
    shapes.MOCK.error404 = (await mockCall(p, 'mockGetPlan', [planId])).error?.status
    await signOut(p)
    await signIn(p, 'nurse01@demo.local')
  }
  await signOut(p)

  // ---------------------------------------------------------------- patient: own plan, no staff details
  await signIn(p, 'patient01@demo.local')
  await p.waitForFunction(() => location.pathname === '/patient')
  await p.waitForSelector('[data-treatment-details]')
  await p.$eval('[data-treatment-details]', (a) => a.click()) // the bottom navigation may cover it at this scroll position
  check(`${mode} patient: 查看療程與給藥紀錄 opens 我的療程`, await p.waitForFunction(() => location.pathname === '/patient/treatment').then(() => true, () => false)
    && await waitSel(p, '[data-my-medications]'), `${await path(p)} ${(await text(p)).slice(0, 200).replace(/\s+/g, ' ')}`)
  const mine = await text(p)
  check(`${mode} patient: plan, cycles and administrations shown`, mine.includes('Cisplatin 同步化療') && mine.includes('Cisplatin 174 mg') && mine.includes('第 1 次'))
  check(`${mode} patient: no nurse name or reaction notes`, !mine.includes('測試護理師') && !mine.includes('反應紀錄'))
  await p.setViewport(phone)
  await idle(p)
  check(`${mode} 390px: 我的療程 has no horizontal overflow`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/m390-${mode}-my-treatment.png`, fullPage: true })
  await go(p, `/nurse/patients/${pid}`)
  check(`${mode} patient: nurse pages redirect home`, (await path(p)) === '/patient')
  await ctx.close()
}

for (const k of ['plans', 'medications', 'drugs', 'regimens']) {
  const diff = shapeDiff(shapes.API[k], shapes.MOCK[k])
  check(`mock / API ${k}: same response structure`, shapes.API[k] && shapes.MOCK[k] && diff.length === 0, diff.join('; '))
}
check('mock / API: another patient\'s plan → 404 in both', shapes.API.error404 === 404 && shapes.MOCK.error404 === 404, `${shapes.API.error404} / ${shapes.MOCK.error404}`)
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
