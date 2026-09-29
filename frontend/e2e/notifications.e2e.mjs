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
const desk = { width: 1440, height: 1500, deviceScaleFactor: 1 }
const DETAIL = 'section[aria-label="通知內容"]'
const LIST = 'section[aria-label="通知列表"]'
const text = (p, sel) => p.$eval(sel, (el) => el.innerText).catch(() => '')
const tabCount = (p, label) => p.evaluate((label) => {
  const b = [...document.querySelectorAll('[role=tab]')].find((x) => x.innerText.trim().startsWith(label))
  return Number(b?.querySelector('span')?.innerText ?? 'NaN')
}, label)
const clickTab = async (p, label) => {
  await p.evaluate((label) => [...document.querySelectorAll('[role=tab]')].find((x) => x.innerText.trim().startsWith(label)).click(), label)
  await idle(p); await sleep(250)
}
const openItem = async (p, title) => {
  await p.evaluate((title) => [...document.querySelectorAll(`section[aria-label="通知列表"] li button[aria-label]`)]
    .find((b) => b.getAttribute('aria-label') === `查看 ${title}`).click(), title)
  await p.waitForSelector(`${DETAIL} h2`)
  await p.waitForFunction((sel, title) => document.querySelector(`${sel} h2`)?.innerText === title, {}, DETAIL, title)
  await idle(p); await sleep(200)
}
const detailAction = async (p) => {
  await p.click(`${DETAIL} button[data-action]`)
  await idle(p); await sleep(400)
}
const shot = async (p, name, sel = 'main') => p.screenshot({ path: `${OUT}/${name}.png`, clip: await p.$eval(sel, (el) => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y + scrollY, width: r.width, height: Math.min(r.height, 1400) } }) })

async function nurseFlow(MODE, base, title, { onStep = async () => {} } = {}) {
  const n = await login(base, 'nurse01@demo.local', desk)
  await n.goto(base + '/nurse/notifications', { waitUntil: 'networkidle0' })
  await n.waitForSelector(LIST)
  await idle(n); await sleep(300)
  const pending0 = await tabCount(n, '待處理')
  const progress0 = await tabCount(n, '處理中')
  check(`${MODE}: Notification Center shows 待處理 / 處理中 / 已完成 tabs with counts`, pending0 >= 1 && !Number.isNaN(progress0), `pending=${pending0} in_progress=${progress0}`)
  const item = await n.evaluate((title) => [...document.querySelectorAll('section[aria-label="通知列表"] li')].find((li) => li.innerText.includes(title))?.innerText, title)
  check(`${MODE}: list item shows 危急, patient, trigger, time, 待處理 and an action button`,
    item && item.includes('危急') && item.includes('待處理') && /（P0000\d）/.test(item) && /(今天|昨天|\d+\/\d+) \d\d:\d\d/.test(item) && item.includes('接手'),
    item?.replace(/\n/g, ' | '))

  // 查看
  await openItem(n, title)
  let d = await text(n, DETAIL)
  check(`${MODE}: detail shows patient, 觸發原因 with value + rule, original record, 建議處理`,
    d.includes('觸發原因') && d.includes('規則：體溫 >= 38') && /原始生命徵象/.test(d) && d.includes('建議處理') && d.includes('急診') && d.includes('病人資料'),
    d.replace(/\n/g, ' | ').slice(0, 260))
  await shot(n, `notif-${MODE}-detail-new`)
  await onStep('new')

  // 接手
  await detailAction(n)
  d = await text(n, DETAIL)
  check(`${MODE}: 接手 → step 2 done by 測試護理師 林; next action 開始處理`, d.includes('測試護理師 林') && d.includes('開始處理'),
    d.split('\n').slice(0, 12).join(' | '))
  const chip = await n.evaluate((title) => [...document.querySelectorAll('section[aria-label="通知列表"] li')].find((li) => li.innerText.includes(title))?.innerText, title)
  check(`${MODE}: list chip updates to 已接手・測試護理師 林 (still under 待處理)`, chip?.includes('已接手・測試護理師 林'), chip?.replace(/\n/g, ' | '))
  await onStep('acknowledged')

  // 處理中
  await detailAction(n)
  check(`${MODE}: 開始處理 → counts move 待處理 -1 / 處理中 +1`, (await tabCount(n, '待處理')) === pending0 - 1 && (await tabCount(n, '處理中')) === progress0 + 1,
    `${await tabCount(n, '待處理')} / ${await tabCount(n, '處理中')}`)
  await clickTab(n, '處理中')
  await openItem(n, title)
  d = await text(n, DETAIL)
  check(`${MODE}: 處理中 tab lists it; detail offers 完成處理 with note field`, d.includes('完成處理') && d.includes('處理說明（內部紀錄'))
  await onStep('in_progress')

  // 完成 (note required)
  await detailAction(n)
  check(`${MODE}: 完成 without a note → inline error, not resolved`, (await text(n, DETAIL)).includes('請填寫處理說明'))
  await n.evaluate((sel) => {
    const ta = document.querySelector(`${sel} textarea`)
    ta.value = '已電話聯繫病人，請病人立即至急診評估'
    ta.dispatchEvent(new Event('input', { bubbles: true }))
  }, DETAIL)
  await detailAction(n)
  d = await text(n, DETAIL)
  check(`${MODE}: 完成 → resolved card with internal note + who`, d.includes('已完成：測試護理師 林') && d.includes('已電話聯繫病人，請病人立即至急診評估')
    && d.includes('病人只會看到'), d.split('\n').filter((l) => l.includes('已完成')).join(' | '))
  await shot(n, `notif-${MODE}-detail-resolved`)
  await clickTab(n, '已完成')
  const done = await text(n, LIST)
  check(`${MODE}: 已完成 tab lists it as 已完成・測試護理師 林`, done.includes(title) && done.includes('已完成・測試護理師 林'))
  await onStep('resolved')

  // mobile layout
  await n.setViewport(phone); await sleep(300)
  const listMode = await n.evaluate(() => document.documentElement.scrollWidth <= innerWidth
    && !!document.querySelector('section[aria-label="通知列表"]')?.offsetParent)
  await n.screenshot({ path: `${OUT}/notif-${MODE}-mobile-list.png` })
  await openItem(n, title)
  const detailMode = await n.evaluate(() => document.documentElement.scrollWidth <= innerWidth
    && !document.querySelector('section[aria-label="通知列表"]')?.offsetParent)
  check(`${MODE}: 390px — no horizontal scroll; list first, then the detail replaces it`, listMode && detailMode, `list=${listMode} detail=${detailMode}`)
  await n.screenshot({ path: `${OUT}/notif-${MODE}-mobile.png` })
  return n
}

// ================================================================== MOCK
console.log('\n===== MOCK =====')
{
  const base = MOCK_URL
  const n = await nurseFlow('MOCK', base, '疑似嗜中性白血球低下發燒')
  // quick action from the list on another alert
  await n.setViewport(desk)
  await clickTab(n, '待處理')
  await n.evaluate(() => [...document.querySelectorAll('section[aria-label="通知列表"] li')].find((li) => li.innerText.includes('疲倦程度偏高'))
    .querySelector('button[data-quick="acknowledge"]').click())
  await idle(n); await sleep(400)
  const row = await n.evaluate(() => [...document.querySelectorAll('section[aria-label="通知列表"] li')].find((li) => li.innerText.includes('疲倦程度偏高'))?.innerText)
  check('MOCK: quick 接手 from the list → 已接手 + next button 開始處理', row?.includes('已接手') && row.includes('開始處理'), row?.replace(/\n/g, ' | '))
  await n.evaluate(() => [...document.querySelectorAll('nav[aria-label="護理端功能"] a')].find((a) => a.innerText.includes('照護總覽')).click())
  await n.waitForFunction(() => document.body.innerText.includes('我的個案')); await idle(n); await sleep(500)
  const dash = await n.evaluate(() => document.body.innerText)
  check('MOCK: dashboard alert list shows lifecycle status and the center link', dash.includes('已接手・測試護理師 林') && dash.includes('在通知中心開啟'))

  const p = await login(base, 'patient02@demo.local', phone)
  await p.waitForSelector('#w-my-notifications')
  const card = await text(p, '#w-my-notifications')
  check('MOCK: patient notification shows reminder + handling status', card.includes('體溫偏高') && card.includes('護理團隊已收到通知'), card.replace(/\n/g, ' | '))
  check('MOCK: patient has no lifecycle buttons', !(await p.evaluate(() => [...document.querySelectorAll('button')].some((b) => /接手|開始處理|完成處理/.test(b.innerText)))))
  await p.goto(base + '/nurse/notifications', { waitUntil: 'networkidle0' })
  check('MOCK: patient opening /nurse/notifications is sent back to /patient', new URL(p.url()).pathname === '/patient', p.url())
}

// ================================================================== API
console.log('\n===== API =====')
{
  const base = WEB_URL
  const pt = await tokenFor('patient01@demo.local')
  const nt = await tokenFor('nurse01@demo.local')
  await api('/vital-signs', pt, { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ patient_id: 'me', temperature_c: 38.6, heart_rate_bpm: 88 }) })
  const staffList = (await api('/notifications?status=pending', nt)).body.data
  const fever = staffList.find((x) => x.alert_rule?.code === 'fever')
  const patientCopy = (await api('/notifications?status=open', pt)).body.data.find((x) => x.event_key === fever.event_key)
  check('API: fever alert created as new for nurse and patient', fever?.status === 'new' && patientCopy?.status === 'new')

  // patient cannot modify (API + UI)
  const denied = await Promise.all(['acknowledge', 'start', 'resolve'].map((a) =>
    api(`/notifications/${patientCopy.id}/${a}`, pt, { method: 'POST', body: JSON.stringify({ resolution_note: 'x' }) })))
  check('API: patient POST acknowledge / start / resolve → 403', denied.every((r) => r.status === 403), denied.map((r) => r.status).join(','))
  const p = await login(base, 'patient01@demo.local', phone)
  await p.waitForSelector('#w-my-notifications')
  check('API: patient UI has no lifecycle buttons', !(await p.evaluate(() => [...document.querySelectorAll('button')].some((b) => /接手|開始處理|完成處理/.test(b.innerText)))))
  const patientStatus = async () => {
    await p.reload({ waitUntil: 'networkidle0' }); await p.waitForSelector('#w-my-notifications'); await sleep(200)
    return p.$eval('#w-my-notifications', (el) => [...el.querySelectorAll('li')].find((li) => li.innerText.includes('化療期間請不要等待'))?.querySelector('[data-status]')?.innerText.trim())
  }

  const seen = {}
  await nurseFlow('API', base, '化療期間發燒', {
    onStep: async (step) => {
      const s = (await api(`/notifications/${fever.id}`, nt)).body.data
      seen[step] = { api: s.status, patient: await patientStatus() }
    },
  })
  check('API: backend status follows each step (new → acknowledged → in_progress → resolved)',
    ['new', 'acknowledged', 'in_progress', 'resolved'].every((k) => seen[k]?.api === k), JSON.stringify(Object.fromEntries(Object.entries(seen).map(([k, v]) => [k, v.api]))))
  check('API: patient sees 護理團隊已收到通知 → 護理師已接手 → 護理師正在處理 → 已處理完成',
    seen.new?.patient === '護理團隊已收到通知' && seen.acknowledged?.patient === '護理師已接手'
    && seen.in_progress?.patient === '護理師正在處理' && seen.resolved?.patient === '已處理完成', JSON.stringify(Object.fromEntries(Object.entries(seen).map(([k, v]) => [k, v.patient]))))
  const page = await p.evaluate(() => document.body.innerText)
  const pd = (await api(`/notifications/${patientCopy.id}`, pt)).body.data
  check('API: patient never sees the internal note or nurse name (page + API)', !page.includes('已電話聯繫病人') && !JSON.stringify(pd).includes('已電話聯繫病人')
    && !JSON.stringify(pd).includes('測試護理師'))
  await p.screenshot({ path: `${OUT}/notif-API-patient.png`, clip: await p.$eval('#w-my-notifications', (el) => { const r = el.getBoundingClientRect(); return { x: 0, y: r.y + scrollY - 8, width: 390, height: r.height + 16 } }) })
}

check('no page errors / console errors', errors.length === 0, errors.join(' || ').slice(0, 300))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
