import puppeteer from 'puppeteer-core'
import { API_URL, CHROME, CHROME_ARGS, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const idle = (p) => p.waitForNetworkIdle({ idleTime: 500, timeout: 6000 }).catch(() => {})
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })

async function login(base, email, viewport) {
  const ctx = await browser.createBrowserContext()
  const page = await ctx.newPage()
  await page.setViewport(viewport)
  await page.goto(base + '/login', { waitUntil: 'networkidle0' })
  await page.type('input[name=email]', email)
  await page.type('input[name=password]', 'Demo@1234')
  await page.click('button[type=submit]')
  await idle(page)
  return page
}
const RISK = 'section[aria-label="今日健康風險摘要"]'
const riskText = (p) => p.$eval(RISK, (el) => el.innerText).catch(() => '')
const phone = { width: 390, height: 1800, deviceScaleFactor: 1 }
const desk = { width: 1440, height: 1600, deviceScaleFactor: 1 }

// ------------------------------------------------------------------ MOCK: stable patient → report symptoms
console.log('\n===== MOCK patient01 (low risk) =====')
let p = await login(MOCK_URL, 'patient01@demo.local', phone)
await p.waitForSelector(RISK)
let t = await riskText(p)
// 2026-10 home order (layouts.py): daily report entry → patient card → 放射線治療進度 → 今日行程 → 集點卡 → risk summary → …
const HOME_ORDER = ['daily-report', 'patient-summary', 'radiation-progress', 'today-schedule', 'self-care-stamps',
  'risk-summary', 'treatment-progress', 'latest-vitals', 'lab-summary', 'symptom-trend']
{
  const ids = await p.$$eval('main [id^=w-]', (els) => els.map((e) => e.id.slice(2)))
  check('MOCK: risk summary is the 6th home block, right after the self-care stamp cards (exact layout order)',
    JSON.stringify(ids) === JSON.stringify(HOME_ORDER) && ids.indexOf('risk-summary') === 5, ids.join(' > '))
}
check('MOCK: low risk → 「今天狀況穩定」 + both next steps', t.includes('今天狀況穩定') && t.includes('回報今天的症狀') && t.includes('量測並記錄生命徵象'))
await p.screenshot({ path: `${OUT}/risk-MOCK-stable.png`, clip: { x: 0, y: 0, width: 390, height: 1000 } })

// a small phone screen (390×560 visible): without scrolling the daily report card sits ~375 px down
// (below 今天的症狀), and the page is tall enough for the router to bring it right under the header
await p.setViewport({ width: 390, height: 560, deviceScaleFactor: 1 })
await p.evaluate(() => [...document.querySelectorAll(`section[aria-label="今日健康風險摘要"] a`)].find((a) => a.innerText.includes('回報今天的症狀')).click())
await p.waitForFunction(() => location.pathname === '/patient/symptoms' && !!document.getElementById('daily-report'))
// wait for the smooth scroll to settle (same scrollY for 300 ms)
await p.waitForFunction(() => {
  const w = window
  if (w.__lastY === scrollY) return ++w.__same >= 6
  w.__lastY = scrollY; w.__same = 0
  return false
}, { polling: 50, timeout: 5000 }).catch(() => {})
const scrolledTo = await p.evaluate(() => {
  const r = document.getElementById('daily-report').getBoundingClientRect()
  const header = Math.round(Math.max(0, ...[...document.querySelectorAll('header')].map((h) => h.getBoundingClientRect().bottom)))
  return { path: location.pathname, hash: location.hash, top: Math.round(r.top), header, scrollY: Math.round(scrollY), vh: innerHeight,
    atEnd: Math.abs(scrollY - (document.documentElement.scrollHeight - innerHeight)) < 2 }
})
check('MOCK: 「回報今天的症狀」 opens 症狀回報 (the report cards moved off the home page)',
  scrolledTo.path === '/patient/symptoms' && scrolledTo.hash === '#daily-report', JSON.stringify(scrolledTo))
check('MOCK: …and the page is actually scrolled to the daily report card (card top in view under the header)',
  // the router aims for 80 px under the top but stops at the end of the page (the card is near the end
  // and short while it loads), so: scrolled, not under the header, and moved into the top half (unscrolled ≈ 375)
  scrolledTo.scrollY > 0 && scrolledTo.top >= scrolledTo.header && scrolledTo.top < scrolledTo.vh / 2,
  JSON.stringify(scrolledTo))
await p.setViewport(phone)

// fill the symptom form: pain 8 → warning alert
await p.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.innerText.trim() === '開始回報').click())
await p.waitForFunction(() => document.querySelectorAll('fieldset').length >= 4)
for (const [q, v] of [['疼痛', '8'], ['噁心', '3'], ['疲倦', '4'], ['發燒', '沒有']]) {
  await p.evaluate((q, v) => {
    const fs = [...document.querySelectorAll('fieldset')].find((f) => f.querySelector('legend').innerText.includes(q))
    ;[...fs.querySelectorAll('label')].find((l) => l.innerText.trim() === v).click()
  }, q, v)
}
await p.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.innerText.trim() === '送出回報').click())
await p.waitForFunction(() => document.body.innerText.includes('已送出，謝謝您'))
await idle(p); await sleep(300)
await p.evaluate(() => [...document.querySelectorAll('nav[aria-label=主要功能] a')].find((a) => a.innerText.trim() === '首頁').click())
await p.waitForSelector(RISK)
await idle(p); await sleep(300)
t = await riskText(p)
check('MOCK: after reporting pain 8 → summary updates: 症狀回報 done with 疼痛 8, alert 處理中, status attention',
  t.includes('已完成') && t.includes('疼痛 8') && t.includes('疼痛程度偏高') && t.includes('今天有些狀況需要留意') && !t.includes('回報今天的症狀'))

// ------------------------------------------------------------------ MOCK: urgent patient
console.log('\n===== MOCK patient02 (high risk) =====')
p = await login(MOCK_URL, 'patient02@demo.local', phone)
await p.waitForSelector(RISK)
t = await riskText(p)
check('MOCK: high risk + open critical alert → urgent, role=alert, call button, nadir badge, reasons',
  t.includes('請立即聯絡醫療團隊') && t.includes('撥打照護專線') && t.includes('目前在骨髓抑制期') && t.includes('骨髓抑制期發燒 38.4°C')
  && (await p.$eval(RISK, (el) => el.getAttribute('role'))) === 'alert' && !!(await p.$(`${RISK} a[href^="tel:"]`)))
await p.screenshot({ path: `${OUT}/risk-MOCK-urgent.png`, clip: { x: 0, y: 0, width: 390, height: 1100 } })

// ------------------------------------------------------------------ MOCK: nurse ranking
console.log('\n===== MOCK nurse ranking =====')
let n = await login(MOCK_URL, 'nurse01@demo.local', desk)
await n.waitForFunction(() => document.body.innerText.includes('我的個案'))
await idle(n)
const order = async () => n.$$eval('section[aria-labelledby="caseload-title"] li button', (bs) =>
  bs.map((b) => b.innerText.match(/P0000\d/)?.[0] + '#' + b.innerText.trim().split('\n')[0]))
let o = await order()
check('MOCK: default 高風險優先 → P00002, P00003, P00001, P00004 with ranks 1–4',
  o.map((x) => x.split('#')[0]).join(',') === 'P00002,P00003,P00001,P00004' && o.map((x) => x.split('#')[1]).join(',') === '1,2,3,4', o.join(' '))
check('MOCK: highest-risk patient opens by default + critical badge', (await n.evaluate(() => document.body.innerText)).includes('危急警示 1'))
await n.select('select[aria-label="個案排序"]', 'last_report'); await idle(n); await sleep(300)
o = (await order()).map((x) => x.split('#')[0])
check('MOCK: 最久未回報 → P00003 (65h), P00001 (16h), P00004 (3h), P00002 (1h)', o.join(',') === 'P00003,P00001,P00004,P00002', o.join(','))
await n.select('select[aria-label="個案排序"]', 'name'); await idle(n); await sleep(300)
o = (await order()).map((x) => x.split('#')[0])
check('MOCK: 病歷代碼 → P00001…P00004', o.join(',') === 'P00001,P00002,P00003,P00004', o.join(','))

// ------------------------------------------------------------------ API: widget mirrors the API result
console.log('\n===== API =====')
const api = async (path, token, opts = {}) => (await fetch(API_URL + path, {
  ...opts, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers ?? {}) },
})).json()
const ptoken = (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email: 'patient01@demo.local', password: 'Demo@1234' }) })).data.access_token
const ntoken = (await api('/auth/login', null, { method: 'POST', body: JSON.stringify({ email: 'nurse01@demo.local', password: 'Demo@1234' }) })).data.access_token
const rsApi = (await api('/dashboard/patient/me', ptoken)).data.widgets['risk-summary']
const engine = (await api('/dashboard/widgets/caseload/data', ntoken)).data[0].risk_level
p = await login(WEB_URL, 'patient01@demo.local', phone)
await p.waitForSelector(RISK)
t = await riskText(p)
check('API: widget shows the API risk-summary title and today counts',
  t.includes(rsApi.title) && t.includes(`今天的提醒 ${rsApi.today.alerts.total} 則`), `${rsApi.level}/${rsApi.status} — ${rsApi.title}`)
check('API: risk-summary.level equals the caseload risk engine level', rsApi.level === engine, `${rsApi.level} vs ${engine}`)
await p.screenshot({ path: `${OUT}/risk-API-patient.png`, clip: { x: 0, y: 0, width: 390, height: 1200 } })
n = await login(WEB_URL, 'nurse01@demo.local', desk)
await n.waitForFunction(() => document.body.innerText.includes('我的個案'))
await idle(n)
const row = await n.$eval('section[aria-labelledby="caseload-title"] li button', (b) => b.innerText)
check('API: caseload row shows rank 1 and alert badge from the API', row.trim().startsWith('1') && /警示/.test(row), row.replace(/\n/g, ' | ').slice(0, 120))

await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
