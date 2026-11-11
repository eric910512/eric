// 皮膚自我評估 (rt_skin_self_assessment, Phase 1a, text only), in mock mode and API mode:
// patient (390 px): 症狀回報 → 皮膚自我評估 entry → fixed notices (self-record, not a diagnosis; not real-time
// monitoring; phone; 119) → the reused daily-report questions + 搔癢 / 破皮或滲液, no grade wording → empty submit is
// flagged → 脫皮伴出血 shows the safety hint, 清除這題 removes it → submit with notes → 已送出 (not "someone has
// seen it") → 「不確定，請護理師協助評估」 alone → history (newest first, notes, review state) → the daily report is
// still not reported today. API only: a failed send is retried with the same Idempotency-Key and stored once.
// nurse: 待審清單 shows 皮膚自我評估 → patient page: answers, 病人補充說明, the 「不確定」 request, no CTCAE picker
// (grading standard pending) → review with a follow-up date → 需追蹤 badge / tab, the patient's answers unchanged
// → patient sees only 護理師已看過 (no note, no follow-up, no grade). Mock / API response structure.
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
const FORM = 'rt_skin_self_assessment'
const QUESTIONS = [
  ['rt_dermatitis_redness', '放射線皮膚炎－發紅情形'],
  ['rt_dermatitis_desquamation', '放射線皮膚炎－脫皮、脫屑情形'],
  ['rt_pain_skin', '疼痛－放射線皮膚發紅、脫皮導致'],
  ['skin_itch', '搔癢'],
  ['skin_broken_exudate', '破皮或有滲液'],
]
const NOTES = '右側頸部照射處，晚上比較癢'

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
const taipeiDate = (daysAhead) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei' }).format(new Date(Date.now() + daysAhead * 86400000))
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
  p.on('console', (m) => m.type() === 'error' && !/Failed to load resource|net::ERR_FAILED/.test(m.text()) && errors.push(`${mode}: ${m.text()}`))
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
  const listSkin = async (status = 'all') => (mode === 'API'
    ? (await api(`/symptoms/records/${pid}?form_code=${FORM}&review_status=${status}`, nt)).body
    : (await mockCall('mockListRecords', [pid, { formCode: FORM, reviewStatus: status }])).body)

  // ---------------------------------------------------------------- patient (390 px)
  await signIn(p, 'patient01@demo.local')
  await go(p, '/patient/symptoms')
  check(`${mode} patient: 症狀回報 has the 皮膚自我評估 entry`, await waitSel(p, '[data-skin-entry]') && (await text(p, '[data-skin-entry]')).includes('皮膚自我評估'))
  check(`${mode} the daily report card is still there (not reported today)`, await waitSel(p, '[data-daily-report][data-state=idle]'))
  await click(p, '[data-skin-entry]')
  check(`${mode} → /patient/skin`, await waitFn(p, () => location.pathname === '/patient/skin') && await waitSel(p, '[data-skin-form] [data-question]'))
  const notice = await text(p, '[data-skin-notice]')
  check(`${mode} notice: a self-record, not a diagnosis`, notice.includes('自我紀錄') && notice.includes('不是醫療診斷'), notice)
  check(`${mode} notice: not a real-time monitoring service, read in service hours`, notice.includes('不是即時醫療監測服務') && notice.includes('不會立即有人查看'))
  check(`${mode} notice: 119 / 急診 and the department phone (tel: link)`, notice.includes('119') && notice.includes('急診')
    && (await p.$eval('[data-skin-notice] a', (a) => a.getAttribute('href')).catch(() => '')) === 'tel:076150011,2205')
  const asked = await p.$$eval('[data-skin-form] [data-question]', (q) => q.map((x) => [x.dataset.question, x.querySelector('legend').childNodes[0].textContent.trim()]))
  check(`${mode} questions: the reused daily-report questions + 搔癢 / 破皮或有滲液, in order`, JSON.stringify(asked) === JSON.stringify(QUESTIONS), JSON.stringify(asked))
  const opts = await p.$$eval('[data-question=rt_dermatitis_desquamation] input', (i) => i.map((x) => x.nextElementSibling.innerText))
  check(`${mode} reused options unchanged (脫皮脫屑)`, opts.join('|') === '無|乾燥、有脫皮脫屑|潮濕、有脫皮脫屑|有脫皮脫屑伴出血', opts.join('|'))
  const pageText = await text(p, '[data-skin-page]')
  check(`${mode} no grade / CTCAE / RTOG for the patient`, !/Grade|CTCAE|RTOG/.test(pageText))
  check(`${mode} 「不確定，請護理師協助評估」 option`, (await text(p, '[data-skin-assist]')).includes('不確定，請護理師協助評估'))

  await click(p, '[data-skin-submit]')
  check(`${mode} empty submit → asked for at least one answer, nothing sent`, await waitSel(p, '[data-skin-missing]'))
  await answer(p, 'rt_dermatitis_desquamation', 'bleeding')
  check(`${mode} 脫皮脫屑伴出血 → safety hint (phone + 119)`, await waitSel(p, '[data-skin-safety]') && (await text(p, '[data-skin-safety]')).includes('119'))
  await p.$eval('[data-question=rt_dermatitis_desquamation] button', (b) => b.click())
  check(`${mode} 清除這題 → answer and hint gone`, await waitFn(p, () => !document.querySelector('[data-skin-safety]')
    && ![...document.querySelectorAll('[data-question=rt_dermatitis_desquamation] input')].some((i) => i.checked)))
  await answer(p, 'skin_broken_exudate', 'yes')
  check(`${mode} 破皮或有滲液：有 → safety hint`, await waitSel(p, '[data-skin-safety]'))
  await answer(p, 'skin_broken_exudate', 'no')
  await answer(p, 'rt_dermatitis_redness', 'light')
  await answer(p, 'rt_pain_skin', 3)
  await answer(p, 'skin_itch', 'yes')
  await setValue(p, '[data-skin-notes]', NOTES)
  check(`${mode} 390 px: no horizontal overflow (form)`, await noOverflow(p))
  await new Promise((r) => setTimeout(r, 500))
  await p.screenshot({ path: `${OUT}/skin-${mode}-patient-form.png`, fullPage: true })

  let keys = []
  if (mode === 'API') {
    // the first send fails on the way (network), the retry keeps its Idempotency-Key → stored once
    await p.setRequestInterception(true)
    let failNext = true
    p.on('request', (r) => {
      if (r.isInterceptResolutionHandled()) return
      if (r.method() === 'POST' && r.url().endsWith('/api/v1/symptoms/records')) {
        keys.push(r.headers()['idempotency-key'])
        if (failNext) { failNext = false; return r.abort('failed') }
      }
      r.continue()
    })
    await click(p, '[data-skin-submit]')
    check(`${mode} network failure → error shown, form kept`, await waitSel(p, '[data-skin-error]') && (await p.$eval('[data-skin-notes]', (t) => t.value)) === NOTES)
  }
  await click(p, '[data-skin-submit]')
  check(`${mode} submit → 已送出, "送出不代表已經有人看過"`, await waitSel(p, '[data-skin-done]') && (await text(p, '[data-skin-done]')).includes('送出不代表已經有人看過'))
  if (mode === 'API') {
    check(`${mode} the retry used the same Idempotency-Key`, keys.length === 2 && keys[0] && keys[0] === keys[1], JSON.stringify(keys))
    check(`${mode} …and the record is stored once`, (await listSkin()).data.length === 1)
  }
  check(`${mode} history: the record with its notes, answers in question order, 護理師會在服務時間內查看`, await waitFn(p, (n) => {
    const li = document.querySelector('[data-skin-history] li')
    const t = li?.innerText ?? ''
    return t.includes(n) && t.includes('搔癢：有') && t.includes('護理師會在服務時間內查看') && t.indexOf('發紅情形') < t.indexOf('疼痛')
  }, NOTES))

  await click(p, '[data-skin-again]')
  await waitSel(p, '[data-skin-form]')
  await click(p, '[data-skin-assist] input')
  await click(p, '[data-skin-submit]')
  check(`${mode} 「不確定，請護理師協助評估」 alone → 已送出`, await waitSel(p, '[data-skin-done]'))
  check(`${mode} history: newest first, two records`, await waitFn(p, () => {
    const li = [...document.querySelectorAll('[data-skin-history] li')]
    return li.length === 2 && li[0].innerText.includes('需要護理師協助評估：是')
  }))
  check(`${mode} 390 px: no horizontal overflow (history)`, await noOverflow(p))
  await p.screenshot({ path: `${OUT}/skin-${mode}-patient-history.png`, fullPage: true })

  await go(p, '/patient/symptoms')
  check(`${mode} daily report still not reported today (skin records do not count)`, await waitSel(p, '[data-daily-report][data-state=idle]'))
  check(`${mode} 我的回報 also lists the skin records`, await waitFn(p, (n) => document.querySelector('[data-my-reports]')?.innerText.includes('搔癢：有'), NOTES))

  const formRes = mode === 'API' ? (await api(`/symptoms/forms/${FORM}`, pt)).body.data : (await p.evaluate(async (f) => (await import('/src/mock/api.js')).mockForms[f], FORM))
  shapes[mode].form = shape(formRes)
  await signOut(p)

  // ---------------------------------------------------------------- nurse
  await p.setViewport(desk)
  await signIn(p, 'nurse01@demo.local')
  await go(p, '/nurse/reviews')
  check(`${mode} 待審清單: the patient's skin self-assessments, labelled 皮膚自我評估`, await waitFn(p, () => [...document.querySelectorAll('[data-queue=symptoms] li')]
    .filter((li) => li.querySelector('[data-queue-form]') && li.innerText.includes('測試病人 甲')).length === 2))
  await go(p, `/nurse/${pid}`)
  check(`${mode} nurse: skin cards on the patient page`, await waitFn(p, () => [...document.querySelectorAll('[data-record-form]')].filter((x) => x.innerText === '皮膚自我評估').length === 2, null, 15000))
  const cards = await p.$$eval('article', (as) => as.filter((a) => a.querySelector('[data-record-form]')?.innerText === '皮膚自我評估').map((a) => a.innerText))
  const withNotes = cards.find((c) => c.includes(NOTES)) ?? ''
  check(`${mode} nurse: answers + 病人補充說明`, withNotes.includes('病人補充說明') && withNotes.includes('搔癢：有') && withNotes.includes('疼痛－放射線皮膚發紅、脫皮導致 3')
    && withNotes.includes('病人自評'), withNotes.slice(0, 200))
  check(`${mode} nurse: the 「不確定」 request stands out`, (await p.$$('[data-assist-request]')).length === 1)
  const before = (await listSkin()).data.find((r) => r.notes === NOTES)
  await p.evaluate((n) => {
    const a = [...document.querySelectorAll('article')].find((x) => x.innerText.includes(n))
    a.setAttribute('data-target', '1')
    ;[...a.querySelectorAll('button')].find((b) => b.innerText.trim() === '審閱').click()
  }, NOTES)
  await waitSel(p, '[data-target] form')
  check(`${mode} nurse: no CTCAE picker for the skin form, grading marked pending`, !(await p.$('[data-target] details'))
    && (await text(p, '[data-target] [data-grade-pending]')).includes('尚待院方確認'))
  await setValue(p, '[data-target] textarea', '已電話衛教保濕與避免摩擦，三天後評估')
  await click(p, '[data-target] [data-follow-up-toggle]')
  await waitSel(p, '[data-target] [data-follow-up-date]')
  await click(p, '[data-target] button[type=submit]')
  check(`${mode} follow-up without a date → asked for it`, await waitFn(p, () => document.querySelector('[data-target] [role=alert]')?.innerText.includes('追蹤日期')))
  await setValue(p, '[data-target] [data-follow-up-date]', taipeiDate(3))
  await click(p, '[data-target] button[type=submit]')
  check(`${mode} reviewed → leaves 待審閱`, await waitFn(p, (n) => ![...document.querySelectorAll('article')].some((x) => x.innerText.includes(n)), NOTES, 15000))
  const after = (await listSkin()).data.find((r) => r.id === before.id)
  check(`${mode} the patient's answers and notes are not overwritten`, JSON.stringify(after.values) === JSON.stringify(before.values)
    && after.notes === before.notes && after.recorded_at === before.recorded_at && after.review_status === 'reviewed')
  check(`${mode} review keeps reviewer, time and follow-up`, after.reviewed_by?.display_name && after.reviewed_at && after.review?.next_follow_up_at)
  await p.evaluate(() => [...document.querySelectorAll('[role=tab]')].find((t) => t.innerText.startsWith('需追蹤')).click())
  check(`${mode} 需追蹤 tab (1): the record, 需追蹤 badge, follow-up date, reviewer's note`, await waitFn(p, (n) => {
    const tab = [...document.querySelectorAll('[role=tab]')].find((t) => t.innerText.startsWith('需追蹤'))
    const a = [...document.querySelectorAll('article')].find((x) => x.innerText.includes(n))
    return tab?.innerText.includes('（1）') && a?.querySelector('[data-review-state=follow_up]') && a.querySelector('[data-follow-up]')
      && a.innerText.includes('三天後評估')
  }, NOTES))
  const fu = await listSkin('follow_up')
  check(`${mode} review_status=follow_up → only that record`, fu.data.length === 1 && fu.data[0].id === before.id && fu.meta.follow_up === 1, JSON.stringify(fu.meta))
  shapes[mode].record = shape(after) // both list the answers in the form's question order
  await p.screenshot({ path: `${OUT}/skin-${mode}-nurse.png`, fullPage: true })
  await p.setViewport(phone)
  await go(p, `/nurse/${pid}`)
  check(`${mode} nurse 390 px: patient page with skin cards, no overflow`, await waitSel(p, '[data-record-form]', 15000) && await noOverflow(p))
  await signOut(p)

  if (mode === 'API') {
    const target = (await listSkin('submitted')).data[0].id
    const adminReview = await api(`/symptoms/records/${target}/review`, at, { method: 'POST', body: JSON.stringify({ action_note: 'x' }) })
    check(`${mode} admin cannot review → 403`, adminReview.status === 403)
    check(`${mode} patient cannot use the 需追蹤 filter → 403`, (await api(`/symptoms/records/me?review_status=follow_up`, pt)).status === 403)
  }

  // ---------------------------------------------------------------- patient: only "reviewed"
  await p.setViewport(phone)
  await signIn(p, 'patient01@demo.local')
  await go(p, '/patient/skin')
  check(`${mode} patient: 護理師已看過這筆紀錄`, await waitFn(p, (n) => [...document.querySelectorAll('[data-skin-history] li')]
    .some((li) => li.innerText.includes(n) && li.innerText.includes('護理師已看過這筆紀錄')), NOTES))
  const hist = await text(p, '[data-skin-history]')
  check(`${mode} patient: no nurse note, no follow-up, no grade`, !hist.includes('三天後評估') && !hist.includes('需追蹤') && !/Grade|CTCAE/.test(hist))
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
