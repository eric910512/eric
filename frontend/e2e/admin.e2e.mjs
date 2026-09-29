// Sprint 7 — admin console, in mock mode and API mode: 管理總覽 (overview numbers), 帳號狀態
// (create admin, disable → cannot sign in, enable, own account protected, login lockout → 解除鎖定),
// 稽核紀錄 (filters, no passwords), 風險規則 (threshold change applies to new records, disable,
// dry run), 症狀量表 (order / required → version +1, patient form follows), 系統設定, nurse / patient
// cannot open the console, 390 px, mock / API response structure.
import crypto from 'node:crypto'

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

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const login = (email, password = 'Demo@1234') => api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password }) })
const tokenFor = async (email, password) => (await login(email, password)).body?.data?.access_token
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
    // audit `changes` and rule `extra_conditions` are free-form objects: compare only that they are objects
    if (/\.changes$|\.extra_conditions$/.test(at)) return []
    const keys = new Set([...Object.keys(a), ...Object.keys(b)])
    return [...keys].flatMap((k) => (!(k in a) ? [`${at}.${k}: API only`] : !(k in b) ? [`${at}.${k}: mock only`] : shapeDiff(a[k], b[k], `${at}.${k}`)))
  }
  return a === b ? [] : [`${at}: ${a} vs ${b}`]
}
const shapes = { MOCK: {}, API: {} }

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const tag = mode.toLowerCase()
  const ADMIN2 = `e2e.admin.${tag}@demo.local`
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(desk)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })

  // ---------------------------------------------------------------- nurse / patient cannot open the console
  await signIn(p, 'nurse01@demo.local')
  await go(p, '/admin/audit')
  check(`${mode} nurse: /admin/audit is not reachable (sent to the nurse workspace)`, await waitFn(p, () => !location.pathname.startsWith('/admin')))
  if (mode === 'API') {
    const nt = await tokenFor('nurse01@demo.local')
    const pt = await tokenFor('patient01@demo.local')
    const denied = await Promise.all([
      api('/admin/overview', nt), api('/admin/audit-logs', nt), api('/admin/users?role=all', pt), api('/notifications/alert-rules', nt),
      api('/admin/users/x', nt, { method: 'PATCH', body: '{"is_active":false}' }), api('/symptoms/forms', pt),
    ])
    check('API nurse / patient: admin endpoints → 403', denied.every((r) => r.status === 403), denied.map((r) => r.status).join(','))
  } else {
    const denied = await p.evaluate(async () => {
      const mock = await import('/src/mock/api.js')
      return ['mockAdminOverview', 'mockSearchAudit', 'mockListRules', 'mockListForms'].map((n) => { try { mock[n](); return 200 } catch (e) { return e.status } })
    })
    check('MOCK nurse: admin handlers → 403', denied.every((s) => s === 403), denied.join(','))
  }
  await p.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.innerText.trim() === '登出')?.click())
  await waitFn(p, () => location.pathname.startsWith('/login'))

  // ---------------------------------------------------------------- 管理總覽
  await signIn(p, 'admin01@demo.local')
  check(`${mode} admin: signs in to 管理總覽`, await waitFn(p, () => location.pathname === '/admin') && await waitSel(p, 'main[data-loaded]'))
  const overviewText = await text(p)
  check(`${mode} admin: overview shows alerts, pending reviews and account tiles`,
    overviewText.includes('尚未處理的風險警示') && overviewText.includes('等待護理師審閱') && !!(await p.$('[data-tile="unassigned"]')) && !!(await p.$('[data-tile="inactive"]')))
  if (mode === 'API') {
    const o = (await api('/admin/overview', await tokenFor('admin01@demo.local'))).body.data
    const shown = await p.$eval('[data-open-alerts]', (e) => Number(e.innerText))
    check('API admin: overview numbers match the API', shown === o.alerts.open, `${shown} vs ${o.alerts.open}`)
  }
  check(`${mode} admin: nav has the console pages`, ['管理總覽', '帳號狀態', '稽核紀錄', '風險規則與量表', '系統設定'].every((t) => overviewText.includes(t)))

  // ---------------------------------------------------------------- 帳號狀態: create admin, disable, enable
  await go(p, '/admin/accounts')
  await waitSel(p, '[data-account-list]')
  const selfRow = await p.$eval('[data-account="admin01@demo.local"]', (e) => ({ text: e.innerText, disable: !!e.querySelector('[data-disable]') }))
  check(`${mode} accounts: own account cannot be disabled from the list`, selfRow.text.includes('目前登入的帳號') && !selfRow.disable)
  check(`${mode} accounts: all roles listed`, ['病人', '護理師', '管理者'].every((r) => selfRow && overviewText) && await p.evaluate(() =>
    ['病人', '護理師', '管理者'].every((r) => [...document.querySelectorAll('[data-account]')].some((li) => li.innerText.includes(r)))))
  await click(p, '[data-new-admin]')
  await setValue(p, '[data-create-admin] input[name=display_name]', `E2E 管理者 ${tag}`)
  await setValue(p, '[data-create-admin] input[name=email]', ADMIN2)
  await click(p, '[data-create-admin] button[type=submit]')
  check(`${mode} accounts: new admin created with a one-time password`, await waitSel(p, '[data-otp-email]'))
  const tempPw = await p.$eval('[data-otp-password]', (e) => e.innerText.trim())
  await click(p, '[data-otp-done]')
  await waitSel(p, `[data-account="${ADMIN2}"]`)
  await click(p, `[data-account="${ADMIN2}"] [data-disable]`)
  await click(p, `[data-account="${ADMIN2}"] [data-confirm-disable]`)
  check(`${mode} accounts: disable → 已停用`, await waitSel(p, `[data-account="${ADMIN2}"] [data-inactive]`) && await waitText(p, '帳號已停用'))
  if (mode === 'API') {
    const r = await login(ADMIN2, tempPw)
    check('API accounts: a disabled account cannot sign in', r.status === 401, `${r.status}`)
  }
  await setValue(p, '[data-account-filters] select[name=status]', 'inactive')
  check(`${mode} accounts: status filter 已停用 lists it`, await waitFn(p, (e) => {
    const rows = [...document.querySelectorAll('[data-account]')]
    return rows.length > 0 && rows.every((r) => r.querySelector('[data-inactive]')) && rows.some((r) => r.dataset.account === e)
  }, ADMIN2))
  await click(p, `[data-account="${ADMIN2}"] [data-enable]`)
  check(`${mode} accounts: enable → active again`, await waitText(p, '帳號已重新啟用') && !(await p.$(`[data-account="${ADMIN2}"] [data-inactive]`)))
  if (mode === 'API') {
    check('API accounts: re-enabled account can sign in', (await login(ADMIN2, tempPw)).status === 200)
    // login lockout (5 wrong passwords) → 登入鎖定中 → 解除鎖定
    for (let i = 0; i < 5; i++) await login(ADMIN2, 'Wrong-pass-1')
    await setValue(p, '[data-account-filters] select[name=status]', 'locked')
    check('API accounts: locked account listed with 登入鎖定', await waitSel(p, `[data-account="${ADMIN2}"] [data-locked]`))
    await click(p, `[data-account="${ADMIN2}"] [data-unlock]`)
    check('API accounts: 解除鎖定 → can sign in again', await waitText(p, '已解除登入鎖定') && (await login(ADMIN2, tempPw)).status === 200)
    const at = await tokenFor('admin01@demo.local')
    const me = (await api('/auth/me', at)).body.data
    const self = await api(`/admin/users/${me.id}`, at, { method: 'PATCH', body: JSON.stringify({ is_active: false }) })
    const role = await api(`/admin/users/${me.id}`, at, { method: 'PATCH', body: JSON.stringify({ role: 'nurse' }) })
    check('API accounts: cannot disable yourself (409) or change a role (400)', self.status === 409 && role.status === 400, `${self.status} / ${role.status}`)
  }
  await setValue(p, '[data-account-filters] select[name=status]', 'any')
  await idle(p)

  // ---------------------------------------------------------------- 風險規則
  await go(p, '/admin/rules')
  await waitSel(p, '[data-rule="tachycardia"]')
  await click(p, '[data-rule="tachycardia"] [data-edit-rule]')
  await setValue(p, '[data-rule="tachycardia"] input[name=threshold_value]', '110')
  await click(p, '[data-rule="tachycardia"] [data-rule-form] button[type=submit]')
  check(`${mode} rules: tachycardia threshold changed to 110`, await waitFn(p, () => document.querySelector('[data-rule="tachycardia"] [data-rule-condition]')?.innerText.includes('110')))
  await click(p, '[data-rule="tachycardia"] [data-try-rule]')
  await setValue(p, '[data-rule="tachycardia"] [data-trial-form] input[name=value]', '115')
  await click(p, '[data-rule="tachycardia"] [data-trial-form] button[type=submit]')
  check(`${mode} rules: dry run 115 → 會產生警示 (nothing sent)`, await waitFn(p, () => document.querySelector('[data-rule="tachycardia"] [data-trial-result]')?.innerText.includes('會產生警示')))
  await click(p, '[data-rule="severe_fatigue"] [data-toggle-rule]')
  check(`${mode} rules: severe_fatigue disabled`, await waitSel(p, '[data-rule="severe_fatigue"][data-active="false"] [data-rule-off]'))
  check(`${mode} rules: a bad threshold is rejected`, await (async () => {
    await click(p, '[data-rule="low_spo2"] [data-edit-rule]')
    await setValue(p, '[data-rule="low_spo2"] input[name=threshold_value]', 'abc')
    await click(p, '[data-rule="low_spo2"] [data-rule-form] button[type=submit]')
    return waitSel(p, '[data-rule="low_spo2"] [data-rule-error]')
  })())
  if (mode === 'API') {
    const pt = await tokenFor('patient01@demo.local')
    const v = await api('/vital-signs', pt, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ patient_id: 'me', heart_rate_bpm: 115 }) })
    const codes = (v.body?.data?.alerts ?? []).map((a) => a.rule_code ?? a.code)
    check('API rules: a new vital (HR 115) now alerts with the changed threshold', v.status === 201 && JSON.stringify(v.body).includes('tachycardia'), `${v.status} ${codes.join(',')}`)
  } else {
    const hit = await p.evaluate(async () => {
      const { mockListRules } = await import('/src/mock/api.js')
      const { threshold, ruleOn } = await import('/src/mock/alertRules.js')
      return { list: mockListRules().data.find((r) => r.code === 'tachycardia').threshold_value, engine: threshold('tachycardia', 120), fatigue: ruleOn('severe_fatigue') }
    })
    check('MOCK rules: mock engines read the changed rule', hit.list === 110 && hit.engine === 110 && hit.fatigue === false, JSON.stringify(hit))
  }

  // ---------------------------------------------------------------- 症狀量表
  await waitSel(p, '[data-form="daily_chemo_check"]')
  const v0 = await p.$eval('[data-form="daily_chemo_check"] [data-form-version]', (e) => e.innerText)
  await click(p, '[data-form="daily_chemo_check"] [data-edit-form]')
  await waitSel(p, '[data-form-editor] [data-item="fever"]')
  await p.$eval('[data-item="fever"] button[aria-label$="往上移"]', (b) => b.click()) // fever: 4 → 3
  await p.$eval('[data-item="fatigue"] input[type=checkbox]', (c) => c.click()) // fatigue no longer required
  await click(p, '[data-save-form]')
  check(`${mode} forms: saved as the next version`, await waitText(p, '已儲存（第') && (await p.$eval('[data-form="daily_chemo_check"] [data-form-version]', (e) => e.innerText)) !== v0,
    `${v0} → ${await p.$eval('[data-form="daily_chemo_check"] [data-form-version]', (e) => e.innerText).catch(() => '')}`)
  const patientForm = mode === 'API'
    ? (await api('/symptoms/forms/daily_chemo_check', await tokenFor('patient01@demo.local'))).body.data
    : await p.evaluate(async () => (await import('/src/mock/api.js')).mockForms.daily_chemo_check)
  const order = patientForm.items.map((i) => i.definition.code).join(',')
  const fatigue = patientForm.items.find((i) => i.definition.code === 'fatigue')
  check(`${mode} forms: the patient form follows (order, required)`, order === 'pain,nausea,fever,fatigue' && fatigue.is_required === false, `${order} ${fatigue?.is_required}`)

  // ---------------------------------------------------------------- 稽核紀錄
  await go(p, '/admin/audit')
  await waitSel(p, '[data-audit-list]')
  await setValue(p, '[data-audit-filters] select[name=resource_type]', 'users')
  await setValue(p, '[data-audit-filters] select[name=action]', 'UPDATE')
  await click(p, '[data-audit-filters] button[type=submit]')
  check(`${mode} audit: filter users / UPDATE → the disable / enable rows`, await waitFn(p, () => {
    const rows = [...document.querySelectorAll('[data-audit-row]')]
    return rows.length >= 2 && rows.every((r) => r.dataset.action === 'UPDATE' && r.dataset.resource === 'users')
  }))
  await setValue(p, '[data-audit-filters] select[name=resource_type]', 'alert_rules')
  await click(p, '[data-audit-filters] button[type=submit]')
  check(`${mode} audit: rule changes are audited`, await waitFn(p, () => [...document.querySelectorAll('[data-audit-row]')].length >= 2
    && [...document.querySelectorAll('[data-audit-row]')].every((r) => r.dataset.resource === 'alert_rules')))
  const auditText = await p.evaluate(() => document.querySelector('[data-audit-list]').textContent)
  check(`${mode} audit: old / new threshold recorded`, auditText.includes('"old"') && auditText.includes('110'))
  await setValue(p, '[data-audit-filters] select[name=resource_type]', '')
  await setValue(p, '[data-audit-filters] select[name=action]', '')
  await click(p, '[data-audit-filters] button[type=submit]')
  await idle(p)
  const allAudit = mode === 'API'
    ? JSON.stringify((await api('/admin/audit-logs?per_page=200', await tokenFor('admin01@demo.local'))).body)
    : await p.evaluate(async () => JSON.stringify((await import('/src/mock/api.js')).mockSearchAudit({ per_page: 200 })))
  check(`${mode} audit: no password or temporary password anywhere`, !!tempPw && !allAudit.includes(tempPw) && !allAudit.includes('Demo@1234') && !/"password"\s*:/.test(allAudit))
  check(`${mode} audit: the audit search itself is audited`, allAudit.includes('audit_logs'))
  await setValue(p, '[data-audit-filters] input[name=from]', '2026-09-20')
  await setValue(p, '[data-audit-filters] input[name=to]', '2026-09-10')
  await click(p, '[data-audit-filters] button[type=submit]')
  check(`${mode} audit: to before from → message`, await waitText(p, '結束日期不能早於開始日期'))

  // ---------------------------------------------------------------- 系統設定
  await go(p, '/admin/settings')
  check(`${mode} settings: institution and security policy (read-only)`, await waitSel(p, '[data-lockout]') && await waitText(p, '連續輸錯 5 次') && (await text(p)).includes('Demo 醫院')
    && !(await p.$('main input, main button[type=submit]')))

  // ---------------------------------------------------------------- 390 px
  await p.setViewport(phone)
  for (const [route, sel] of [['/admin', 'main[data-loaded]'], ['/admin/accounts', '[data-account-list]'], ['/admin/audit', '[data-audit-list]'], ['/admin/rules', '[data-rule-list]'], ['/admin/settings', '[data-security]']]) {
    await go(p, route)
    await waitSel(p, sel)
    check(`${mode} 390px: ${route} has no horizontal overflow`, await noOverflow(p))
    await p.screenshot({ path: `${OUT}/m390-${mode}-admin${route.replace('/admin', '').replace('/', '-') || '-home'}.png`, fullPage: true })
  }

  // ---------------------------------------------------------------- shapes
  if (mode === 'API') {
    const at = await tokenFor('admin01@demo.local')
    const users = (await api('/admin/users?role=all', at)).body
    shapes.API.overview = shape((await api('/admin/overview', at)).body)
    shapes.API.users = shape(users)
    shapes.API.patch = shape((await api(`/admin/users/${users.data.find((u) => u.email === ADMIN2).id}`, at, { method: 'PATCH', body: JSON.stringify({ display_name: 'E2E 管理者' }) })).body)
    shapes.API.audit = shape((await api('/admin/audit-logs?per_page=5', at)).body)
    shapes.API.settings = shape((await api('/admin/settings', at)).body)
    shapes.API.rules = shape((await api('/notifications/alert-rules', at)).body)
    shapes.API.trial = shape((await api('/notifications/alert-rules/7/test', at, { method: 'POST', body: JSON.stringify({ value: 130 }) })).body)
    shapes.API.forms = shape((await api('/symptoms/forms', at)).body)
  } else {
    const m = await p.evaluate(async (email) => {
      const mock = await import('/src/mock/api.js')
      const users = mock.mockListStaff({ role: 'all' })
      return {
        overview: mock.mockAdminOverview(), users, patch: mock.mockUpdateAccount(users.data.find((u) => u.email === email).id, { display_name: 'E2E 管理者' }),
        audit: mock.mockSearchAudit({ per_page: 5 }), settings: mock.mockAdminSettings(), rules: mock.mockListRules(),
        trial: mock.mockTestRule(7, { value: 130 }), forms: mock.mockListForms(),
      }
    }, ADMIN2)
    for (const [k, v] of Object.entries(m)) shapes.MOCK[k] = shape(v)
  }
  await ctx.close()
}

for (const k of ['overview', 'users', 'patch', 'audit', 'settings', 'rules', 'trial', 'forms']) {
  const diff = shapeDiff(shapes.API[k], shapes.MOCK[k])
  check(`mock / API ${k}: same response structure`, shapes.API[k] && shapes.MOCK[k] && diff.length === 0, diff.join('; '))
}
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
