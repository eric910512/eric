// 批量建立教學帳號, in mock mode and API mode: admin opens the page from the menu → preview (creates
// nothing) → a 7-character password is refused (normal policy) → confirm → 12 pairs created in two
// requests of 10 → results with patient codes and primary assignments → CSV (UTF-8 BOM, no password) →
// a second preview shows 已存在 → students sign in with the shared password without a forced change →
// nurse sees only their own patient, patient only themselves → non-admins get 403 → mock / API structure.
import fs from 'node:fs'
import path from 'node:path'

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
const PASSWORD = 'Train2026x' // test-only value (never a real account password)
const COUNT = 12

const api = async (p, token, opts = {}) => {
  const r = await fetch(API_URL + p, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const tokenFor = async (email, password = 'Demo@1234') => (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password }) })).body?.data?.access_token
const text = (p, sel = 'body') => p.$eval(sel, (e) => e.innerText).catch(() => '')
const waitSel = (p, sel, timeout = 10000) => p.waitForSelector(sel, { timeout }).then(() => true, () => false)
const waitText = (p, s, timeout = 10000) => p.waitForFunction((s) => document.body.innerText.includes(s), { timeout }, s).then(() => true, () => false)
const waitFn = (p, fn, arg, timeout = 10000) => p.waitForFunction(fn, { timeout }, arg).then(() => true, () => false)
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
async function signIn(p, email, password = 'Demo@1234') {
  await go(p, '/login')
  await p.waitForSelector('input[name=email]')
  await setValue(p, 'input[name=email]', email)
  await setValue(p, 'input[name=password]', password)
  await p.click('button[type=submit]')
  await idle(p)
}
const signOut = async (p) => {
  await p.evaluate(() => [...document.querySelectorAll('button, a')].find((b) => b.innerText.trim() === '登出')?.click())
  await waitFn(p, () => location.pathname.startsWith('/login'))
}
function shape(v) {
  if (Array.isArray(v)) return v.length ? [shape(v[0])] : []
  if (v && typeof v === 'object') return Object.fromEntries(Object.keys(v).sort().map((k) => [k, shape(v[k])]))
  return v === null ? null : typeof v
}
const shapes = { MOCK: {}, API: {} }
const SPEC = { patient_prefix: 'patientfyu', nurse_prefix: 'nursefyu', domain: 'demo.local', patient_name_prefix: '學生病人', nurse_name_prefix: '學生護理師' }

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const downloads = path.join(OUT, `training-csv-${mode.toLowerCase()}`)
  fs.rmSync(downloads, { recursive: true, force: true })
  fs.mkdirSync(downloads, { recursive: true })
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  const cdp = await p.createCDPSession()
  await cdp.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: downloads, browserContextId: ctx.id, eventsEnabled: true })
  p.on('pageerror', (e) => errors.push(`${mode}: ${e.message}`))
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource|status of 4\d\d/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
  await p.setViewport(desk)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })
  const mockCall = (name, args) => p.evaluate(async (a) => {
    try {
      return { status: 200, body: await (await import('/src/mock/api.js'))[a.name](...a.args) }
    } catch (e) {
      return { status: e.status ?? 500, body: { error: { code: e.code, message: e.message, details: e.details } } }
    }
  }, { name, args })

  // ---------------------------------------------------------------- non-admin
  await signIn(p, 'nurse01@demo.local')
  await go(p, '/admin/training-accounts')
  check(`${mode} nurse cannot open the page (redirected to own home)`, await waitFn(p, () => !location.pathname.startsWith('/admin')))
  const nurseCall = mode === 'API'
    ? await api('/admin/training-accounts/preview', await tokenFor('nurse01@demo.local'), { method: 'POST', body: JSON.stringify({ ...SPEC, start: 1, count: 1 }) })
    : await mockCall('mockTrainingPreview', [{ ...SPEC, start: 1, count: 1 }])
  check(`${mode} nurse calling the API directly → 403`, nurseCall.status === 403, nurseCall.status)
  await signOut(p)

  // ---------------------------------------------------------------- admin: preview → confirm → create
  await signIn(p, 'admin01@demo.local')
  await p.evaluate(() => [...document.querySelectorAll('nav[aria-label="管理功能"] a')].find((a) => a.innerText.includes('批量建立教學帳號'))?.click())
  check(`${mode} admin menu → 批量建立教學帳號`, await waitFn(p, () => location.pathname === '/admin/training-accounts') && await waitSel(p, '[data-training-form]'))
  await setValue(p, '[data-training-form] input[name=count]', String(COUNT))
  await click(p, '[data-training-preview]')
  check(`${mode} preview: ${COUNT} rows 將建立 (accounts and names as planned)`, await waitSel(p, '[data-training-table]')
    && (await p.$$eval('[data-training-table] tbody tr[data-status=will_create]', (r) => r.length)) === COUNT
    && (await text(p, '[data-training-table] tr[data-row="001"]')).includes('patientfyu001@demo.local')
    && (await text(p, '[data-training-table] tr[data-row="012"]')).includes('學生護理師 012'))
  const nothing = mode === 'API'
    ? (await api('/admin/users?role=all&q=fyu', await tokenFor('admin01@demo.local'))).body.data.length
    : (await mockCall('mockListStaff', [{ role: 'all', q: 'fyu' }])).body.data.length
  check(`${mode} preview created nothing`, nothing === 0, nothing)

  await setValue(p, '[data-training-confirm] input[name=password]', 'fyu0000')
  await p.$eval('[data-training-confirm] input[name=confirm]', (c) => c.click())
  await click(p, '[data-training-create]')
  check(`${mode} 7-character password refused (normal 8–128 rule), nothing created`, await waitSel(p, '[data-training-error]')
    && (await text(p, '[data-training-confirm]')).includes('must be 8–128 characters'))
  await setValue(p, '[data-training-confirm] input[name=password]', PASSWORD)
  if (!(await p.$eval('[data-training-confirm] input[name=confirm]', (c) => c.checked))) await p.$eval('[data-training-confirm] input[name=confirm]', (c) => c.click())
  await click(p, '[data-training-create]')
  check(`${mode} create → 成功 ${COUNT}`, await waitSel(p, '[data-training-results]', 60000)
    && (await text(p, '[data-training-summary]')).includes(`成功 ${COUNT}`), await text(p, '[data-training-summary]'))
  const codes = await p.$$eval('[data-training-table] tbody tr', (rows) => rows.map((r) => [r.dataset.row, r.dataset.status, r.querySelector('[data-code]').innerText, r.innerText.includes('主責')]))
  check(`${mode} every row: created, patient code, primary assignment`, codes.length === COUNT && codes.every(([, s, c, primary]) => s === 'created' && /^P\d{5}$/.test(c) && primary)
    && new Set(codes.map((x) => x[2])).size === COUNT, JSON.stringify(codes.slice(0, 2)))
  check(`${mode} the password is not shown anywhere on the page`, !(await text(p)).includes(PASSWORD))

  await click(p, '[data-training-csv]')
  await waitFn(p, () => true)
  let csv = ''
  for (let i = 0; i < 20 && !csv; i++) {
    await new Promise((r) => setTimeout(r, 250))
    const f = fs.readdirSync(downloads).find((x) => x.endsWith('.csv'))
    if (f) csv = fs.readFileSync(path.join(downloads, f), 'utf8')
  }
  const lines = csv.replace(/^﻿/, '').trim().split(/\r?\n/)
  check(`${mode} CSV: UTF-8 BOM, header + ${COUNT} rows, accounts / codes / 主責, no password`, csv.startsWith('﻿') && lines.length === COUNT + 1
    && lines[0].includes('病人帳號') && lines[1].includes('patientfyu001@demo.local') && lines[1].includes('nursefyu001@demo.local')
    && lines[1].includes('是') && !csv.includes(PASSWORD) && !lines[0].includes('密碼'), lines.slice(0, 2).join(' | '))

  // re-run preview → 已存在
  await p.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.innerText.includes('建立另一批'))?.click())
  await setValue(p, '[data-training-form] input[name=count]', String(COUNT))
  await click(p, '[data-training-preview]')
  check(`${mode} preview again → all 已存在 (nothing would be changed)`, await waitFn(p, (n) =>
    document.querySelectorAll('[data-training-table] tbody tr[data-status=exists]').length === n, COUNT))

  // structure
  const spec = { ...SPEC, start: 1, count: 2 }
  shapes[mode].preview = shape(mode === 'API'
    ? (await api('/admin/training-accounts/preview', await tokenFor('admin01@demo.local'), { method: 'POST', body: JSON.stringify(spec) })).body
    : (await mockCall('mockTrainingPreview', [spec])).body)
  shapes[mode].create = shape(mode === 'API'
    ? (await api('/admin/training-accounts', await tokenFor('admin01@demo.local'), { method: 'POST', body: JSON.stringify({ ...spec, password: PASSWORD, confirm: true }) })).body
    : (await mockCall('mockTrainingCreate', [{ ...spec, password: PASSWORD, confirm: true }])).body)
  await signOut(p)

  // ---------------------------------------------------------------- students: no forced change, isolation
  await signIn(p, 'nursefyu001@demo.local', PASSWORD)
  check(`${mode} nursefyu001 signs in with the shared password, no 設定新密碼`, await waitFn(p, () => location.pathname.startsWith('/nurse')) && !(await text(p)).includes('設定新密碼'))
  await go(p, '/nurse/patients')
  check(`${mode} nursefyu001 sees only 學生病人 001`, await waitText(p, '學生病人 001') && !(await text(p)).includes('學生病人 002') && !(await text(p)).includes('P00001'))
  await signOut(p)
  if (mode === 'API') {
    const n1 = await tokenFor('nursefyu001@demo.local', PASSWORD)
    const p1 = await tokenFor('patientfyu001@demo.local', PASSWORD)
    const at = await tokenFor('admin01@demo.local')
    const all = (await api('/patients?per_page=100', at)).body.data
    const idOf = (name) => all.find((x) => x.display_name === name || x.patient_code === name)?.id
    const blocked = await Promise.all([idOf('學生病人 002'), idOf('P00001')].map(async (id) => [
      (await api(`/patients/${id}`, n1)).status, (await api(`/patients/${id}`, p1)).status]))
    check('API nursefyu001 / patientfyu001 → 學生病人 002 and P00001: 404', blocked.flat().every((s) => s === 404), JSON.stringify(blocked))
    check('API nursefyu002 cannot see 學生病人 001', (await api(`/patients/${idOf('學生病人 001')}`, await tokenFor('nursefyu002@demo.local', PASSWORD))).status === 404)
  }
  await signIn(p, 'patientfyu001@demo.local', PASSWORD)
  check(`${mode} patientfyu001 signs in, no forced password change, own home`, await waitFn(p, () => location.pathname === '/patient') && !(await text(p)).includes('設定新密碼'))
  await signOut(p)
  await ctx.close()
}

for (const k of ['preview', 'create']) {
  const same = JSON.stringify(shapes.API[k]) === JSON.stringify(shapes.MOCK[k])
  check(`mock / API ${k}: same response structure`, shapes.API[k] && same, `${JSON.stringify(shapes.API[k])} vs ${JSON.stringify(shapes.MOCK[k])}`)
}
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
