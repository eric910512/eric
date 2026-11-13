// 護理師追蹤管理 (Phase 1c), in mock mode and API mode:
// patient sends two skin self-assessments → nurse reviews the first with a follow-up (date, time, reason), the
// second without, then 建立追蹤 on the reviewed card (reason required; one open follow-up per record — 409) →
// 追蹤管理: tabs with counts, rows (patient, report date, reason, due in Taipei time, state, creator), detail with
// history → 改期 (reason required; old → new date kept) → another nurse changes it meanwhile → stale action gets
// "已被其他護理師更新" and the current state → 結案 with result / actions and a next follow-up (chain, previous
// link) → 取消 with a reason → counts per tab, sort, patient filter → 390 px + mobile entry → patient: read-only
// 照護追蹤 with the 119 notice, no nurse notes → patient / admin cannot use nurse endpoints → mock / API shapes.
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
const phone = { width: 390, height: 844, deviceScaleFactor: 1 }
const MOCK_P1 = '72ba2de4-f19d-4daf-96f9-03a5e83c07d0'

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const tokenFor = async (email) => (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password: 'Demo@1234' }) })).body.data.access_token
const text = (p, sel = 'body') => p.$eval(sel, (e) => e.innerText).catch(() => '')
const waitSel = (p, sel, timeout = 10000) => p.waitForSelector(sel, { timeout }).then(() => true, () => false)
const waitFn = (p, fn, arg, timeout = 15000) => p.waitForFunction(fn, { timeout }, arg).then(() => true, () => false)
const noOverflow = (p) => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const count = (p, sel) => p.$$eval(sel, (els) => els.length)
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
const taipeiDate = (days) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei' }).format(new Date(Date.now() + days * 86400000))
const md = (d) => `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`
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
    return [...keys].flatMap((k) => (!(k in a) ? [`${at}.${k}: mock only`] : !(k in b) ? [`${at}.${k}: API only`] : shapeDiff(a[k], b[k], `${at}.${k}`)))
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
  let pt, nt, at
  if (mode === 'API') {
    pt = await tokenFor('patient01@demo.local')
    nt = await tokenFor('nurse01@demo.local')
    at = await tokenFor('admin01@demo.local')
    pid = (await api('/patients?q=P00001', nt)).body.data[0].id
  }
  const mockCall = (name, args) => p.evaluate(async (a) => {
    try {
      return { status: 200, body: await (await import('/src/mock/api.js'))[a.name](...a.args) }
    } catch (e) {
      return { status: e.status ?? 500, body: { error: { code: e.code, message: e.message, details: e.details } } }
    }
  }, { name, args })

  // ---------------------------------------------------------------- patient: two skin self-assessments
  await signIn(p, 'patient01@demo.local')
  await go(p, '/patient/skin')
  await waitSel(p, '[data-skin-form] [data-question]')
  await answer(p, 'skin_broken_exudate', 'yes')
  await click(p, '[data-skin-submit]')
  await waitSel(p, '[data-skin-done]')
  await click(p, '[data-skin-again]')
  await answer(p, 'skin_itch', 'yes')
  await click(p, '[data-skin-submit]')
  check(`${mode} patient sent two skin self-assessments`, await waitSel(p, '[data-skin-done]'))
  await signOut(p)

  // ---------------------------------------------------------------- nurse: review with a follow-up
  await p.setViewport(desk)
  await signIn(p, 'nurse01@demo.local')
  await go(p, `/nurse/${pid}`)
  const skinCards = () => p.$$('article')
  await waitFn(p, () => [...document.querySelectorAll('article')].filter((a) => a.querySelector('[data-record-form]')?.innerText === '皮膚自我評估').length === 2)
  const mark = (n) => p.evaluate((n) => {
    const a = [...document.querySelectorAll('article')].filter((x) => x.querySelector('[data-record-form]')?.innerText === '皮膚自我評估')
    document.querySelectorAll('[data-t]').forEach((x) => x.removeAttribute('data-t'))
    a[n].setAttribute('data-t', '1')
    return a[n].innerText
  }, n)
  const firstText = await mark(1) // the older one (破皮或有滲液：有)
  check(`${mode} (fixture) the older card is the 破皮 report`, firstText.includes('破皮或有滲液：有'))
  await p.evaluate(() => [...document.querySelectorAll('[data-t] button')].find((b) => b.innerText.trim() === '審閱').click())
  await waitSel(p, '[data-t] form')
  await setValue(p, '[data-t] textarea', '已電話衛教換藥方式')
  await click(p, '[data-t] [data-follow-up-toggle]')
  await waitSel(p, '[data-t] [data-follow-up-date]')
  await setValue(p, '[data-t] [data-follow-up-date]', taipeiDate(2))
  await setValue(p, '[data-t] [data-follow-up-time]', '10:00')
  await click(p, '[data-t] button[type=submit]')
  check(`${mode} follow-up without a reason → asked for it, nothing sent`, await waitFn(p, () => document.querySelector('[data-t] [role=alert]')?.innerText.includes('追蹤原因')))
  await setValue(p, '[data-t] [data-follow-up-reason]', '觀察破皮滲液')
  await click(p, '[data-t] button[type=submit]')
  await waitFn(p, () => !document.querySelector('[data-t]'))
  await p.evaluate(() => [...document.querySelectorAll('[role=tab]')].find((t) => t.innerText.startsWith('需追蹤')).click())
  check(`${mode} reviewed with a follow-up → 需追蹤 with date and time, link to 追蹤管理`, await waitFn(p, (d) => {
    const a = [...document.querySelectorAll('article')].find((x) => x.querySelector('[data-review-state=follow_up]'))
    return a && a.querySelector('[data-follow-up]')?.innerText.includes(`${d} 10:00`) && !!a.querySelector('[data-follow-up-link]')
  }, md(taipeiDate(2))))

  // second card: review without a follow-up, then 建立追蹤
  await p.evaluate(() => [...document.querySelectorAll('[role=tab]')].find((t) => t.innerText.startsWith('待審閱')).click())
  await waitFn(p, () => [...document.querySelectorAll('article')].some((a) => a.querySelector('[data-record-form]')?.innerText === '皮膚自我評估'))
  await mark(0)
  await p.evaluate(() => [...document.querySelectorAll('[data-t] button')].find((b) => b.innerText.trim() === '審閱').click())
  await waitSel(p, '[data-t] form')
  await setValue(p, '[data-t] textarea', '已了解搔癢情形')
  await click(p, '[data-t] button[type=submit]')
  await waitFn(p, () => !document.querySelector('[data-t]'))
  await p.evaluate(() => [...document.querySelectorAll('[role=tab]')].find((t) => t.innerText.startsWith('已審閱')).click())
  await waitFn(p, () => [...document.querySelectorAll('article')].some((a) => a.querySelector('[data-follow-up-create]')))
  await p.evaluate(() => {
    const a = [...document.querySelectorAll('article')].find((x) => x.querySelector('[data-follow-up-create]'))
    a.setAttribute('data-t', '1')
    a.querySelector('[data-follow-up-create]').click()
  })
  await waitSel(p, '[data-t] [data-follow-up-create-form]')
  await setValue(p, '[data-t] [data-create-date]', taipeiDate(3))
  await click(p, '[data-t] [data-create-submit]')
  check(`${mode} 建立追蹤 without a reason → asked for it`, await waitFn(p, () => document.querySelector('[data-t] [data-create-error]')?.innerText.includes('追蹤原因')))
  await setValue(p, '[data-t] [data-create-reason]', '一週內確認搔癢是否改善')
  await click(p, '[data-t] [data-create-submit]')
  check(`${mode} 建立追蹤 on a reviewed record → the card shows 需追蹤, the create button is gone`, await waitFn(p, () =>
    [...document.querySelectorAll('article [data-follow-up]')].length >= 1 && ![...document.querySelectorAll('[data-follow-up-create]')].length))

  // duplicate open follow-up → 409
  const recs = mode === 'API' ? (await api(`/symptoms/records/${pid}?form_code=rt_skin_self_assessment`, nt)).body.data
    : (await mockCall('mockListRecords', [pid, { formCode: 'rt_skin_self_assessment' }])).body.data
  const recWithFu = recs.find((r) => r.follow_up?.status === 'open')
  const dupBody = { symptom_record_id: recWithFu.id, due_at: `${taipeiDate(5)}T09:00:00+08:00`, reason: '重複' }
  const dup = mode === 'API' ? await api('/care-follow-ups', nt, { method: 'POST', body: JSON.stringify(dupBody), headers: { 'Idempotency-Key': crypto.randomUUID() } })
    : await mockCall('mockCreateFollowUp', [dupBody, crypto.randomUUID()])
  check(`${mode} a second open follow-up for the same record → 409 FOLLOW_UP_EXISTS`, dup.status === 409 && dup.body.error.code === 'FOLLOW_UP_EXISTS')

  // ---------------------------------------------------------------- 追蹤管理
  await go(p, '/nurse/follow-ups')
  check(`${mode} 追蹤管理: 待追蹤 (2), rows sorted by due date`, await waitFn(p, () => document.querySelectorAll('[data-follow-up-row]').length === 2
    && document.querySelector('[data-follow-up-tab=open]')?.innerText.includes('（2）')))
  const rows = await p.$$eval('[data-follow-up-row]', (r) => r.map((x) => x.innerText))
  check(`${mode} row: patient, report date, reason, due (Taipei time), state, creator`, rows[0].includes('測試病人 甲') && rows[0].includes('P00001')
    && rows[0].includes('觀察破皮滲液') && rows[0].includes(`${taipeiDate(2)} 10:00`) && rows[0].includes('待追蹤') && rows[0].includes('建立') && rows[0].includes('回報'), rows[0])
  await p.$eval('[data-follow-up-row]', (b) => b.click())
  check(`${mode} detail with its history (建立)`, await waitSel(p, '[data-follow-up-detail] [data-follow-up-event=created]')
    && (await text(p, '[data-follow-up-due]')).includes(`${taipeiDate(2)} 10:00`))
  const fuId = await p.evaluate(() => new URLSearchParams(location.search).get('id'))

  // 改期
  await click(p, '[data-follow-up-action=reschedule]')
  await waitSel(p, '[data-follow-up-dialog]')
  await setValue(p, '[data-dialog-date]', taipeiDate(4))
  await setValue(p, '[data-dialog-time]', '14:30')
  await click(p, '[data-dialog-submit]')
  check(`${mode} 改期 without a reason → error, dialog stays`, await waitSel(p, '[data-dialog-error]') && !!(await p.$('[data-follow-up-dialog]')))
  await setValue(p, '[data-dialog-reason]', '病人改約下午')
  await click(p, '[data-dialog-submit]')
  check(`${mode} 改期 → new date shown; history keeps old → new and the reason`, await waitFn(p, (d) => !document.querySelector('[data-follow-up-dialog]')
    && document.querySelector('[data-follow-up-due]')?.innerText.includes(`${d} 14:30`)
    && document.querySelector('[data-follow-up-event=rescheduled]')?.innerText.includes('病人改約下午'), taipeiDate(4)))
  const ev = await text(p, '[data-follow-up-event=rescheduled]')
  check(`${mode} …the original date is still visible in the history`, ev.includes(`${taipeiDate(2)} 10:00`) && ev.includes(`${taipeiDate(4)} 14:30`), ev)

  // another nurse changes it meanwhile → stale action refused, current state shown
  const cur = mode === 'API' ? (await api(`/care-follow-ups/${fuId}`, nt)).body.data : (await mockCall('mockFollowUp', [fuId])).body.data
  const other = { due_at: `${taipeiDate(5)}T08:00:00+08:00`, reason: '另一位護理師改期', version: cur.version }
  if (mode === 'API') await api(`/care-follow-ups/${fuId}/reschedule`, nt, { method: 'PATCH', body: JSON.stringify(other), headers: { 'Idempotency-Key': crypto.randomUUID() } })
  else await mockCall('mockRescheduleFollowUp', [fuId, other, crypto.randomUUID()])
  await click(p, '[data-follow-up-action=cancel]')
  await waitSel(p, '[data-follow-up-dialog]')
  await setValue(p, '[data-dialog-reason]', '以舊畫面取消')
  await click(p, '[data-dialog-submit]')
  check(`${mode} stale version → "已被其他護理師更新", the current date is reloaded, nothing cancelled`, await waitSel(p, '[data-follow-up-conflict]')
    && await waitFn(p, (d) => document.querySelector('[data-follow-up-due]')?.innerText.includes(`${d} 08:00`), taipeiDate(5))
    && (await text(p, '[data-follow-up-detail-state]')) === '待追蹤')
  await p.keyboard.press('Escape')
  await p.evaluate(() => document.querySelector('[data-follow-up-dialog]')?.dispatchEvent(new MouseEvent('click', { bubbles: true })))
  await waitFn(p, () => !document.querySelector('[data-follow-up-dialog]'))

  // 結案 with a next follow-up
  await click(p, '[data-follow-up-action=complete]')
  await waitSel(p, '[data-follow-up-dialog]')
  await setValue(p, '[data-dialog-result]', '滲液減少，傷口邊緣乾燥')
  await setValue(p, '[data-dialog-actions]', '衛教換藥與保持乾燥')
  await click(p, '[data-dialog-next]')
  await waitSel(p, '[data-dialog-next-date]')
  await setValue(p, '[data-dialog-next-date]', taipeiDate(9))
  await setValue(p, '[data-dialog-next-reason]', '一週後再確認傷口')
  await click(p, '[data-dialog-submit]')
  check(`${mode} 結案 + next → the new open follow-up is shown, linked to the previous one`, await waitFn(p, (id) =>
    new URLSearchParams(location.search).get('id') !== id && !!document.querySelector('[data-follow-up-previous]')
    && document.querySelector('[data-follow-up-detail-state]')?.innerText === '待追蹤', fuId))
  await click(p, '[data-follow-up-previous]')
  check(`${mode} the previous one: 已完成, result, signed nursing record, link to the next`, await waitFn(p, () =>
    document.querySelector('[data-follow-up-detail-state]')?.innerText === '已完成'
    && document.querySelector('[data-follow-up-result]')?.innerText.includes('滲液減少') && !!document.querySelector('[data-follow-up-next-link]')
    && !!document.querySelector('[data-follow-up-event=completed]') && !document.querySelector('[data-follow-up-action]')))

  // 取消 the other one
  await click(p, '[data-follow-up-tab=open]')
  await waitFn(p, () => [...document.querySelectorAll('[data-follow-up-row]')].some((r) => r.innerText.includes('搔癢')))
  await p.evaluate(() => [...document.querySelectorAll('[data-follow-up-row]')].find((r) => r.innerText.includes('搔癢')).click())
  await waitSel(p, '[data-follow-up-action=cancel]')
  await click(p, '[data-follow-up-action=cancel]')
  await waitSel(p, '[data-follow-up-dialog]')
  await setValue(p, '[data-dialog-reason]', '病人已回診處理')
  await click(p, '[data-dialog-submit]')
  check(`${mode} 取消 → 已取消, reason in the history, no actions left`, await waitFn(p, () =>
    document.querySelector('[data-follow-up-detail-state]')?.innerText === '已取消' && !!document.querySelector('[data-follow-up-event=cancelled]')
    && !document.querySelector('[data-follow-up-action]')))
  check(`${mode} counts: 待追蹤 1 / 已逾期 0 / 已完成 1 / 已取消 1 / 全部 3`, await waitFn(p, () => {
    const t = (k) => document.querySelector(`[data-follow-up-tab=${k}]`)?.innerText ?? ''
    return t('open').includes('（1）') && t('overdue').includes('（0）') && t('completed').includes('（1）') && t('cancelled').includes('（1）') && t('all').includes('（3）')
  }))
  await click(p, '[data-follow-up-tab=all]')
  await waitFn(p, () => document.querySelectorAll('[data-follow-up-row]').length === 3)
  await p.select('[data-follow-up-sort]', '-due_at')
  const desc = await waitFn(p, () => {
    const due = [...document.querySelectorAll('[data-follow-up-row]')].map((r) => r.querySelector('.text-right span').innerText)
    return due.length === 3 && due.join() === [...due].sort().reverse().join()
  })
  check(`${mode} sort by due date (far → near)`, desc)
  await p.select('[data-follow-up-patient]', pid)
  check(`${mode} patient filter`, await waitFn(p, () => [...document.querySelectorAll('[data-follow-up-row]')].every((r) => r.innerText.includes('P00001'))))
  await p.screenshot({ path: `${OUT}/follow-up-${mode}-nurse.png`, fullPage: true })

  if (mode === 'MOCK') {
    // a Phase 1a review date (only on the review, no follow-up item): shown on the card and offered for 建立追蹤
    const legacyDay = taipeiDate(3)
    await p.evaluate(async (a) => {
      const m = await import('/src/mock/api.js')
      const rec = m.submitMockReport(a.pid, { form_code: 'rt_skin_self_assessment', values: [{ definition_code: 'skin_itch', option_code: 'no' }] })
      m.mockReview(rec.id, { action_note: '第一階段的審閱', assessment_type: 'phone_follow_up' })
      const nr = await import('/src/mock/nurseReview.js')
      Object.values(nr.symptomRecordStore).flat().find((r) => r.id === rec.id).review.next_follow_up_at = new Date(`${a.day}T15:00:00+08:00`).toISOString()
    }, { pid, day: legacyDay })
    await go(p, '/nurse')
    await go(p, `/nurse/${pid}`)
    await p.evaluate(() => [...document.querySelectorAll('[role=tab]')].find((t) => t.innerText.startsWith('需追蹤')).click())
    check(`${mode} Phase 1a date without a follow-up item: listed under 需追蹤, shown as 尚未建立追蹤事項`, await waitFn(p, (d) =>
      [...document.querySelectorAll('[data-follow-up-legacy]')].some((x) => x.innerText.includes(`${d} 15:00`) && x.innerText.includes('尚未建立追蹤事項')), md(legacyDay)))
    await p.evaluate(() => [...document.querySelectorAll('article')].find((a) => a.querySelector('[data-follow-up-legacy]')).querySelector('[data-follow-up-create]').click())
    check(`${mode} …建立追蹤 starts from that date (nothing converted until the nurse confirms)`, await waitFn(p, (d) =>
      document.querySelector('[data-create-date]')?.value === d, legacyDay))
  }

  const detailShape = mode === 'API' ? (await api(`/care-follow-ups/${fuId}`, nt)).body.data : (await mockCall('mockFollowUp', [fuId])).body.data
  shapes[mode].detail = shape(detailShape)
  const listShape = mode === 'API' ? (await api('/care-follow-ups?status=all', nt)).body : (await mockCall('mockListFollowUps', [{ status: 'all' }])).body
  shapes[mode].list = shape(listShape)
  const evShape = mode === 'API' ? (await api(`/care-follow-ups/${fuId}/events`, nt)).body.data : (await mockCall('mockFollowUpEvents', [fuId])).body.data
  shapes[mode].events = shape(evShape)

  await p.setViewport(phone)
  await go(p, '/nurse')
  check(`${mode} 390 px: 追蹤管理 entry on the nurse home`, await waitSel(p, '[data-follow-up-mobile-entry]'))
  await click(p, '[data-follow-up-mobile-entry]')
  check(`${mode} 390 px: follow-ups page, no horizontal overflow`, await waitSel(p, '[data-follow-up-row]') && await noOverflow(p))
  await p.screenshot({ path: `${OUT}/follow-up-${mode}-nurse-390.png`, fullPage: true })
  await signOut(p)

  // ---------------------------------------------------------------- patient: read only
  await signIn(p, 'patient01@demo.local')
  await go(p, '/patient/skin')
  check(`${mode} patient: 照護追蹤 with the notice (not real-time monitoring, 119)`, await waitSel(p, '[data-my-follow-ups] [data-follow-up-notice]')
    && (await text(p, '[data-follow-up-notice]')).includes('並非即時監測') && (await text(p, '[data-follow-up-notice]')).includes('119'))
  const states = await p.$$eval('[data-my-follow-up]', (l) => l.map((x) => x.dataset.myFollowUp).sort())
  check(`${mode} patient: 已安排 / 已完成 / 已取消 shown`, JSON.stringify(states) === JSON.stringify(['cancelled', 'completed', 'open']), JSON.stringify(states))
  const mine = await text(p, '[data-my-follow-ups]')
  check(`${mode} patient: no nurse reasons, results or names`, !/觀察破皮|滲液減少|病人已回診|測試護理師/.test(mine))
  check(`${mode} patient: each report shows its follow-up state`, (await count(p, '[data-skin-follow-up]')) === 2)
  check(`${mode} patient: no buttons to change a follow-up`, (await count(p, '[data-follow-up-action], [data-follow-up-create]')) === 0)
  check(`${mode} 390 px: patient page no horizontal overflow`, await noOverflow(p))
  const mineShape = mode === 'API' ? (await api('/patients/me/care-follow-ups', pt)).body : (await mockCall('mockMyFollowUps', [])).body
  shapes[mode].mine = shape(mineShape)
  if (mode === 'API') {
    check(`${mode} patient / admin cannot use the nurse endpoints → 403`, (await api('/care-follow-ups', pt)).status === 403
      && (await api(`/care-follow-ups/${fuId}`, at)).status === 403
      && (await api(`/care-follow-ups/${fuId}/cancel`, pt, { method: 'POST', body: JSON.stringify({ reason: 'x', version: 9 }), headers: { 'Idempotency-Key': crypto.randomUUID() } })).status === 403)
  }
  await signOut(p)
  await ctx.close()
}

for (const k of ['detail', 'list', 'events', 'mine']) {
  const diff = shapeDiff(shapes.MOCK[k], shapes.API[k])
  check(`mock / API ${k}: same response structure`, shapes.MOCK[k] && shapes.API[k] && diff.length === 0, diff.join('; '))
}
check('no page errors', errors.length === 0, errors.slice(0, 5).join(' | '))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
