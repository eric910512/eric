// 病人與護理師聊天室 (Phase 1, text only), in mock mode and API mode:
// patient (390 px): 聊天室 button on every patient page, above the bottom navigation, never covering
// it or the page's controls once scrolled (incl. 衛教影片) → chat page with the fixed 義大醫院放射腫瘤科
// hours + phone → send → own bubble right + one system auto reply (styled as system, not a nurse) →
// second message: no second auto reply → reload keeps the history → Idempotency-Key replay → failed
// send + 重新傳送 (one message only) → slow first load hint (sleeping API) → no overflow.
// nurse: 病人訊息 in the navigation with unread count → inbox → conversation → reply → unread cleared
// (also at 390 px). patient: unread badge on the button → reply as 放射腫瘤科護理師 (no nurse name),
// new messages by polling without a reload. Fixed emergency notice (119) on the patient chat page, never a
// chat message. Nurse at 390 px: 病人訊息 entry on the home page → inbox → conversation → reply. Isolation: other patient 404, unassigned nurse 404, admin 403.
// API only: three first-of-the-day messages at the same time → exactly one auto reply.
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
const desktop = { width: 1280, height: 900, deviceScaleFactor: 1 }
const MOCK_P1 = '72ba2de4-f19d-4daf-96f9-03a5e83c07d0'
const MOCK_P2 = 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e' // patient02 (not patient01's)
const PHONE_TEXT = '07-6150011 分機 2205'
const EMERGENCY = '本聊天室非即時緊急醫療服務。如有呼吸困難、意識不清等緊急狀況，請立即撥打 119 或就近急診就醫。'
const AUTO_START = '您好！這裡是義大醫院放射腫瘤科'
const TRAIN_PW = 'Train1234'

const api = async (path, token, opts = {}) => {
  const r = await fetch(API_URL + path, { ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) } })
  return { status: r.status, body: await r.json().catch(() => null) }
}
const tokenFor = async (email, password = 'Demo@1234') => (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email, password }) })).body?.data?.access_token
const send = (token, pid, body, key = crypto.randomUUID()) => api(`/patients/${pid}/messages`, token, { method: 'POST', body: JSON.stringify({ body }), headers: { 'Idempotency-Key': key } })
const text = (p, sel = 'body') => p.$eval(sel, (e) => e.innerText).catch(() => '')
const waitSel = (p, sel, timeout = 10000, visible = false) => p.waitForSelector(sel, { timeout, visible }).then(() => true, () => false)
const waitFn = (p, fn, arg, timeout = 10000) => p.waitForFunction(fn, { timeout }, arg).then(() => true, () => false)
const count = (p, sel) => p.$$eval(sel, (els) => els.length)
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
async function sendInUi(p, message) {
  await setValue(p, '#chat-input', message)
  await p.click('[data-chat-composer] button[type=submit]')
}

/** The 聊天室 button: inside the screen, above the bottom navigation, nav items still clickable. */
const fabGeometry = (p) => p.evaluate(() => {
  const fab = document.querySelector('[data-chat-fab] a')?.getBoundingClientRect()
  const nav = document.querySelector('nav[aria-label="主要功能"]')?.getBoundingClientRect()
  if (!fab || !nav) return null
  const navTargets = [...document.querySelectorAll('nav[aria-label="主要功能"] a')].map((a) => {
    const r = a.getBoundingClientRect()
    return a.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2))
  })
  return { fab: { top: Math.round(fab.top), bottom: Math.round(fab.bottom), left: Math.round(fab.left), right: Math.round(fab.right) },
    navTop: Math.round(nav.top), vw: innerWidth, vh: innerHeight, navClickable: navTargets.every(Boolean) }
})
const fabOk = (g) => g && g.fab.bottom <= g.navTop - 4 && g.fab.left >= 0 && g.fab.right <= g.vw && g.fab.top > g.vh / 2 && g.navClickable
/** Scrolled to the end: no control of the page lies under the button. */
const coveredAtEnd = async (p) => {
  await p.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
  await new Promise((r) => setTimeout(r, 300))
  return p.evaluate(() => {
    const f = document.querySelector('[data-chat-fab] a').getBoundingClientRect()
    return [...document.querySelectorAll('main a, main button, main input, main textarea, main select, main video, main [role=button]')]
      .filter((el) => {
        const r = el.getBoundingClientRect()
        return r.width && r.height && r.left < f.right && r.right > f.left && r.top < f.bottom && r.bottom > f.top
      })
      .map((el) => `${el.tagName.toLowerCase()}:${(el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 20)}`)
  })
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

  let pid = MOCK_P1
  let at, nt, pt
  if (mode === 'API') {
    at = await tokenFor('admin01@demo.local')
    nt = await tokenFor('nurse01@demo.local')
    pt = await tokenFor('patient01@demo.local')
    pid = (await api('/patients?q=P00001', nt)).body.data[0].id
  }

  // ---------------------------------------------------------------- patient: the button on every patient page
  await signIn(p, 'patient01@demo.local')
  check(`${mode}: patient signed in → home`, await waitFn(p, () => location.pathname === '/patient'))
  check(`${mode}: 聊天室 button on the home page`, await waitSel(p, '[data-chat-fab] a'))
  await p.waitForSelector('[data-education-video]', { timeout: 10000 }).catch(() => {})
  let g = await fabGeometry(p)
  check(`${mode} 390 px: button bottom-right, above the bottom navigation, navigation still clickable`, fabOk(g), JSON.stringify(g))
  check(`${mode} 390 px: home has no horizontal overflow (with the button)`, await noOverflow(p))
  const videoFree = await p.evaluate(async () => {
    const v = document.querySelector('[data-education-video] video')
    v.scrollIntoView({ block: 'center' })
    await new Promise((r) => setTimeout(r, 200))
    const a = v.getBoundingClientRect(), f = document.querySelector('[data-chat-fab] a').getBoundingClientRect()
    return !(a.left < f.right && a.right > f.left && a.top < f.bottom && a.bottom > f.top)
  }).catch(() => false)
  check(`${mode} 390 px: 衛教影片 fully visible next to the button (not covered)`, videoFree)
  let covered = await coveredAtEnd(p)
  check(`${mode} 390 px: home scrolled to the end — no control under the button`, covered.length === 0, covered.join(', '))
  await p.screenshot({ path: `${OUT}/chat-${mode}-home-fab.png` })

  for (const [path, name] of [['/patient/symptoms', '症狀回報'], ['/patient/timeline', '健康紀錄'], ['/patient/notifications', '通知'], ['/patient/me', '我的'], ['/patient/treatment', '我的療程']]) {
    await go(p, path)
    const ok = await waitSel(p, '[data-chat-fab] a', 8000)
    g = ok ? await fabGeometry(p) : null
    covered = ok ? await coveredAtEnd(p) : ['(no button)']
    check(`${mode} 390 px: ${name} page — button shown, above the navigation, nothing covered at the end, no overflow`,
      ok && fabOk(g) && covered.length === 0 && await noOverflow(p), `${JSON.stringify(g)} ${covered.join(', ')}`)
  }

  // ---------------------------------------------------------------- patient: chat page
  await go(p, '/patient')
  await p.waitForSelector('[data-chat-fab] a')
  await p.$eval('[data-chat-fab] a', (a) => a.click())
  check(`${mode}: button → /patient/chat`, await waitFn(p, () => location.pathname === '/patient/chat'))
  check(`${mode}: no 聊天室 button on the chat page itself`, (await count(p, '[data-chat-fab]')) === 0)
  const header = await text(p, '[data-chat-service]')
  check(`${mode}: fixed service hours + phone on top (義大醫院放射腫瘤科)`, header.includes('09:00-12:30') && header.includes('13:30-17:30') && header.includes(PHONE_TEXT)
    && (await text(p, 'header')).includes('義大醫院放射腫瘤科'), header.replace(/\n/g, ' | '))
  check(`${mode}: phone number is a tel: link with the extension`, (await p.$eval('[data-chat-service] a', (a) => a.getAttribute('href')).catch(() => '')) === 'tel:076150011,2205')
  const notice = await text(p, 'header [data-chat-emergency]')
  check(`${mode}: fixed emergency notice on top (119 / 急診)`, notice.trim() === EMERGENCY, notice)
  check(`${mode}: empty conversation`, await waitSel(p, '[data-chat-empty]'))

  await sendInUi(p, '您好，我今天皮膚有點紅，需要擦藥嗎？')
  check(`${mode}: sent → own bubble on the right`, await waitFn(p, () => document.querySelectorAll('[data-side=own][data-message-id]').length === 1))
  check(`${mode}: first message today → one system auto reply`, await waitFn(p, () => document.querySelectorAll('[data-chat-system]').length === 1))
  const sys = await text(p, '[data-chat-system]')
  check(`${mode}: auto reply = the 義大醫院 text, labelled 系統自動回覆, not a nurse reply`,
    sys.includes(AUTO_START) && sys.includes(PHONE_TEXT) && sys.includes('系統自動回覆') && sys.includes('並非護理師本人回覆') && !sys.includes('放射腫瘤科護理師\n'), sys.slice(0, 80))
  const sides = await p.evaluate(() => {
    const own = document.querySelector('[data-side=own] p').getBoundingClientRect()
    const list = document.querySelector('[data-chat-messages]').getBoundingClientRect()
    return { ownRight: Math.round(list.right - own.right), ownLeft: Math.round(own.left - list.left) }
  })
  check(`${mode}: own message aligned right`, sides.ownRight <= 2 && sides.ownLeft > 20, JSON.stringify(sides))
  await sendInUi(p, '另外想問明天幾點治療？')
  await waitFn(p, () => document.querySelectorAll('[data-side=own][data-message-id]').length === 2)
  await new Promise((r) => setTimeout(r, 600))
  check(`${mode}: second message the same day → still one auto reply`, (await count(p, '[data-side=own][data-message-id]')) === 2 && (await count(p, '[data-chat-system]')) === 1)
  const composer = await p.$eval('[data-chat-composer]', (f) => { const r = f.getBoundingClientRect(); return { bottom: Math.round(r.bottom), vh: innerHeight } })
  check(`${mode} 390 px: composer at the bottom of the screen`, composer.bottom <= composer.vh && composer.bottom >= composer.vh - 4, JSON.stringify(composer))
  check(`${mode} 390 px: chat page has no horizontal overflow`, await noOverflow(p))
  const top = await p.evaluate(() => {
    scrollTo(0, document.body.scrollHeight)
    const h = document.querySelector('header').getBoundingClientRect(), n = document.querySelector('[data-chat-emergency]').getBoundingClientRect()
    return { headerBottom: Math.round(h.bottom), noticeTop: Math.round(n.top), noticeHeight: Math.round(n.height), vh: innerHeight }
  })
  check(`${mode} 390 px: emergency notice stays on screen, header ≤ 30% of the screen`,
    top.noticeTop >= 0 && top.noticeHeight <= 60 && top.headerBottom <= top.vh * 0.3, JSON.stringify(top))
  check(`${mode}: emergency notice is not a chat message (auto reply text unchanged)`,
    !(await text(p, '[data-chat-messages]')).includes('119') && !sys.includes('119'))
  await p.screenshot({ path: `${OUT}/chat-${mode}-patient.png` })

  await p.reload({ waitUntil: 'networkidle0' })
  check(`${mode}: reload keeps the history, no extra auto reply`,
    await waitFn(p, () => document.querySelectorAll('[data-side=own][data-message-id]').length === 2 && document.querySelectorAll('[data-chat-system]').length === 1))
  check(`${mode}: date separator (今天) shown`, (await text(p, '[data-chat-messages]')).includes('今天'))

  // Idempotency-Key: the same key returns the same message
  const dupKey = `e2e-dup-${mode}`
  const r1 = mode === 'API' ? (await send(pt, 'me', '同一個請求', dupKey)).body.data : (await mockCall('mockSendChatMessage', ['me', '同一個請求', dupKey])).body.data
  const r2 = mode === 'API' ? (await send(pt, 'me', '同一個請求', dupKey)).body.data : (await mockCall('mockSendChatMessage', ['me', '同一個請求', dupKey])).body.data
  check(`${mode}: same Idempotency-Key → same message, not a second one`, r1?.message?.id && r1.message.id === r2?.message?.id)

  // isolation (patient)
  if (mode === 'API') {
    const other = (await api('/patients', at, { method: 'POST', body: JSON.stringify({ display_name: '聊天隔離', gender: 'female', date_of_birth: '1970-01-01' }) })).body.data
    const codes = [(await api(`/patients/${other.id}/messages`, pt)).status, (await send(pt, other.id, '偷看')).status]
    check(`${mode}: patient → another patient's conversation → 404 (read and send)`, codes.every((c) => c === 404), codes.join(','))
  } else {
    const codes = [(await mockCall('mockChatMessages', [MOCK_P2])).status, (await mockCall('mockSendChatMessage', [MOCK_P2, '偷看', 'k-x'])).status]
    check(`${mode}: patient → another patient's conversation → 404 (read and send)`, codes.every((c) => c === 404), codes.join(','))
  }

  if (mode === 'API') {
    // failed send (network) → 重新傳送 → exactly one message
    await p.reload({ waitUntil: 'networkidle0' })
    await p.waitForSelector('[data-side=own][data-message-id]')
    const before = (await api('/patients/me/messages?limit=100', pt)).body.data.length
    let dropped = false
    const drop = (req) => {
      if (!dropped && req.method() === 'POST' && /\/patients\/me\/messages$/.test(req.url())) {
        dropped = true
        req.abort('failed')
      } else req.continue()
    }
    await p.setRequestInterception(true)
    p.on('request', drop)
    await sendInUi(p, '這則第一次會失敗')
    check(`${mode}: network failure → 傳送失敗 + 重新傳送`, await waitSel(p, '[data-chat-failed]'))
    p.off('request', drop)
    await p.setRequestInterception(false)
    await p.evaluate(() => [...document.querySelectorAll('[data-chat-failed] button')].find((b) => b.innerText.includes('重新傳送'))?.click())
    check(`${mode}: 重新傳送 → delivered`, await waitFn(p, () => !document.querySelector('[data-chat-failed]') && !document.querySelector('[data-chat-sending]')
      && [...document.querySelectorAll('[data-side=own][data-message-id]')].some((li) => li.innerText.includes('這則第一次會失敗'))))
    check(`${mode}: …stored exactly once`, (await api('/patients/me/messages?limit=100', pt)).body.data.length === before + 1)

    // a slow first request (sleeping API) → a hint instead of a blank page
    const slow = (req) => {
      if (req.method() === 'GET' && /\/patients\/me\/messages\?limit=30$/.test(req.url())) setTimeout(() => req.continue(), 6000)
      else req.continue()
    }
    await p.setRequestInterception(true)
    p.on('request', slow)
    await p.reload({ waitUntil: 'domcontentloaded' })
    check(`${mode}: slow first load → "伺服器可能正在喚醒" hint`, await waitSel(p, '[data-chat-slow]', 8000))
    check(`${mode}: …then the conversation appears`, await waitSel(p, '[data-side=own][data-message-id]', 15000))
    p.off('request', slow)
    await p.setRequestInterception(false)
  }
  await signOut(p)

  // ---------------------------------------------------------------- nurse: 病人訊息
  await p.setViewport(desktop)
  await signIn(p, 'nurse01@demo.local')
  const nurseName = ((await text(p)).match(/(\S+)（護理師）/) ?? [])[1]
  check(`${mode}: nurse navigation has 病人訊息 with the unread count`, await waitFn(p, () => {
    const a = [...document.querySelectorAll('aside nav a')].find((x) => x.innerText.includes('病人訊息'))
    return a && Number(a.querySelector('[data-chat-unread]')?.innerText) > 0
  }))
  await p.evaluate(() => [...document.querySelectorAll('aside nav a')].find((x) => x.innerText.includes('病人訊息')).click())
  check(`${mode}: → /nurse/messages (not the /nurse/:patientId page)`, await waitFn(p, () => location.pathname === '/nurse/messages') && await waitSel(p, '[data-chat-inbox]'))
  check(`${mode}: inbox lists the assigned patient with unread messages`, await waitSel(p, `[data-inbox-patient="${pid}"] [data-inbox-unread]`))
  await p.$eval(`[data-inbox-patient="${pid}"] a`, (a) => a.click())
  check(`${mode}: conversation opens`, await waitFn(p, (pid) => location.pathname === `/nurse/messages/${pid}`, pid) && await waitSel(p, '[data-side=other][data-message-id]'))
  const nurseView = await text(p, '[data-chat-messages]')
  check(`${mode}: nurse sees the patient's messages and the system auto reply`, nurseView.includes('皮膚有點紅') && (await count(p, '[data-chat-system]')) === 1)
  await sendInUi(p, '您好，可以先擦醫師開的藥膏，明天治療時讓我們看看。')
  check(`${mode}: nurse reply → own bubble`, await waitFn(p, () => [...document.querySelectorAll('[data-side=own][data-message-id]')].some((li) => li.innerText.includes('擦醫師開的藥膏'))))
  check(`${mode}: reading clears the patient's unread count (inbox + navigation)`, await waitFn(p, (pid) =>
    !document.querySelector(`[data-inbox-patient="${pid}"] [data-inbox-unread]`) && !document.querySelector('aside [data-chat-unread]'), pid, 15000))
  check(`${mode}: inbox preview shows the reply at once (護理師：…)`, await waitFn(p, (pid) =>
    (document.querySelector(`[data-inbox-patient="${pid}"]`)?.innerText ?? '').includes('護理師：您好，可以先擦'), pid))
  await p.screenshot({ path: `${OUT}/chat-${mode}-nurse.png` })
  check(`${mode}: desktop — the mobile 病人訊息 entry is hidden (navigation is shown)`,
    (await go(p, '/nurse'), await waitSel(p, '[data-chat-mobile-entry]', 5000, false)) && !(await p.$eval('[data-chat-mobile-entry]', (a) => a.offsetParent)))
  await p.setViewport(phone)
  await go(p, '/nurse')
  check(`${mode} 390 px: nurse home has a visible 病人訊息 entry (side navigation hidden)`,
    await waitSel(p, '[data-chat-mobile-entry]', 10000, true) && !(await p.$eval('aside', (a) => a.offsetParent).catch(() => null)) && await noOverflow(p))
  await p.screenshot({ path: `${OUT}/chat-${mode}-nurse-home-390.png` })
  await p.$eval('[data-chat-mobile-entry]', (a) => a.click())
  check(`${mode} 390 px: entry → nurse inbox, no overflow`,
    await waitFn(p, () => location.pathname === '/nurse/messages') && await waitSel(p, `[data-inbox-patient="${pid}"]`) && await noOverflow(p))
  check(`${mode} 390 px: inbox has a way back to 照護總覽`, (await text(p, '[data-chat-inbox]')).includes('照護總覽'))
  await p.$eval(`[data-inbox-patient="${pid}"] a`, (a) => a.click())
  check(`${mode} 390 px: conversation opens, no overflow`,
    await waitFn(p, (pid) => location.pathname === `/nurse/messages/${pid}`, pid) && await waitSel(p, '[data-side=other][data-message-id]') && await noOverflow(p))
  await sendInUi(p, '補充：擦藥前請先用清水輕洗。')
  check(`${mode} 390 px: nurse reply from the phone → own bubble`,
    await waitFn(p, () => [...document.querySelectorAll('[data-side=own][data-message-id]')].some((li) => li.innerText.includes('清水輕洗'))))
  await p.screenshot({ path: `${OUT}/chat-${mode}-nurse-390.png` })

  // isolation (nurse / admin)
  if (mode === 'API') {
    const r = await api('/admin/training-accounts', at, { method: 'POST', body: JSON.stringify({ start: 1, count: 1, patient_prefix: 'e2echatpt', nurse_prefix: 'e2echatnu',
      domain: 'demo.local', patient_name_prefix: '聊天病人', nurse_name_prefix: '聊天護理師', password: TRAIN_PW, confirm: true }) })
    check(`${mode}: setup — a second training pair`, r.status === 200 || r.status === 201, String(r.status))
    const ntB = await tokenFor('e2echatnu001@demo.local', TRAIN_PW)
    const ptB = await tokenFor('e2echatpt001@demo.local', TRAIN_PW)
    const codes = [(await api(`/patients/${pid}/messages`, ntB)).status, (await send(ntB, pid, 'x')).status]
    const inboxB = (await api('/chat/inbox', ntB)).body.data.map((x) => x.patient.id)
    check(`${mode}: nurse not assigned to patient01 → 404 (read / send), not in their inbox`, codes.every((c) => c === 404) && !inboxB.includes(pid), codes.join(','))
    const adminCodes = [(await api(`/patients/${pid}/messages`, at)).status, (await send(at, pid, 'x')).status, (await api('/chat/inbox', at)).status]
    check(`${mode}: admin → 403 (read / send / inbox)`, adminCodes.every((c) => c === 403), adminCodes.join(','))
    // three "first message of the day" at the same time → one auto reply
    const sent = await Promise.all([1, 2, 3].map((i) => send(ptB, 'me', `同時送出 ${i}`)))
    const all = (await api('/patients/me/messages?limit=100', ptB)).body.data
    check(`${mode}: three messages sent at the same time → all stored, exactly one auto reply`,
      sent.every((s) => s.status === 201) && all.filter((m) => m.sender_role === 'patient').length === 3 && all.filter((m) => m.kind === 'auto_reply').length === 1,
      `${sent.map((s) => s.status)} / ${all.map((m) => m.kind).join(',')}`)
  } else {
    const inbox = (await mockCall('mockChatInbox', [])).body.data.map((x) => x.patient.id)
    check(`${mode}: nurse inbox has only assigned patients`, inbox.includes(MOCK_P1) && inbox.length >= 1)
  }
  await signOut(p)

  // ---------------------------------------------------------------- patient: the reply arrives
  await p.setViewport(phone)
  await signIn(p, 'patient01@demo.local')
  check(`${mode}: unread badge on the 聊天室 button (2 nurse replies)`, await waitFn(p, () => document.querySelector('[data-chat-fab] [data-chat-unread]')?.innerText.trim() === '2'))
  await p.$eval('[data-chat-fab] a', (a) => a.click())
  check(`${mode}: the nurse's reply is shown on the left`, await waitFn(p, () => [...document.querySelectorAll('[data-side=other]')].some((li) => li.innerText.includes('擦醫師開的藥膏'))))
  const replyLabel = await p.evaluate(() => [...document.querySelectorAll('[data-side=other]')].find((li) => li.innerText.includes('擦醫師開的藥膏'))?.querySelector('span')?.innerText.trim())
  check(`${mode}: reply labelled 放射腫瘤科護理師, the nurse's name never shown`, replyLabel === '放射腫瘤科護理師' && !!nurseName && !(await text(p)).includes(nurseName), `${replyLabel} / ${nurseName}`)
  if (mode === 'API') {
    await send(nt, pid, '補充：今天下午也可以打電話到門診詢問。')
    check(`${mode}: a new reply appears by polling, without a reload (≤ 15 s)`,
      await waitFn(p, () => [...document.querySelectorAll('[data-side=other]')].some((li) => li.innerText.includes('也可以打電話')), null, 15000))
  }
  await go(p, '/patient')
  check(`${mode}: after reading, no unread badge on the button`, await waitSel(p, '[data-chat-fab] a') && await waitFn(p, () => !document.querySelector('[data-chat-fab] [data-chat-unread]')))
  await signOut(p)

  if (mode === 'MOCK') {
    await signIn(p, 'admin01@demo.local')
    const codes = [(await mockCall('mockChatMessages', [MOCK_P1])).status, (await mockCall('mockSendChatMessage', [MOCK_P1, 'x', 'k-admin'])).status, (await mockCall('mockChatInbox', [])).status]
    check(`${mode}: admin → 403 (read / send / inbox)`, codes.every((c) => c === 403), codes.join(','))
    await signOut(p)
  }
  await ctx.close()
}

check('no page errors', errors.length === 0, errors.join(' || ').slice(0, 400))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
