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
  page.posts = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && r.url().endsWith('/api/v1/vital-signs')) page.posts.push(r.headers()['idempotency-key'])
  })
  await page.goto(base + '/login', { waitUntil: 'networkidle0' })
  await page.type('input[name=email]', email)
  await page.type('input[name=password]', 'Demo@1234')
  await page.click('button[type=submit]')
  await idle(page)
  return page
}
const text = (p) => p.evaluate(() => document.body.innerText)
const fieldInput = (label) => `xpath/.//label[normalize-space(.)="${label}"]/following-sibling::div//input`
async function fill(p, label, value) {
  const [el] = await p.$$(fieldInput(label))
  await el.evaluate((e) => { e.value = ''; e.dispatchEvent(new Event('input', { bubbles: true })) })
  if (value !== '') await el.type(String(value))
}
const clickLabel = (p, t) => p.evaluate((t) => {
  const el = [...document.querySelectorAll('label span, button, a')].find((x) => x.innerText.trim() === t)
  if (!el) throw new Error(`"${t}" not found`)
  el.click()
}, t)

const CASES = {
  MOCK: { base: MOCK_URL, patient: 'patient02@demo.local', feverRule: '疑似嗜中性白血球低下發燒', restricted: true },
  API: { base: WEB_URL, patient: 'patient01@demo.local', feverRule: '化療期間發燒', restricted: false },
}

for (const [mode, c] of Object.entries(CASES)) {
  console.log(`\n===== ${mode} (patient) =====`)
  const p = await login(c.base, c.patient, { width: 390, height: 1600, deviceScaleFactor: 1 })
  await p.waitForFunction(() => document.body.innerText.includes('記錄生命徵象'), { timeout: 10000 })
  await clickLabel(p, '記錄生命徵象')
  await p.waitForFunction(() => location.pathname === '/patient/vitals')
  await idle(p)
  check(`${mode}: dashboard link opens /patient/vitals`, (await text(p)).includes('量完就填'))

  if (c.restricted) {
    const picked = await p.$eval('input[value=left_arm]', (el) => el.checked)
    const t = await text(p)
    check('MOCK: arm defaults to left because right arm is restricted; restriction shown',
      picked && t.includes('右手（禁止）') && t.includes('右手禁止注射及量血壓'))
    await clickLabel(p, '右手（禁止）')
    await sleep(150)
    check('MOCK: choosing the restricted arm turns the note into a red warning', (await text(p)).includes('請改量另一側'))
    await clickLabel(p, '左手')
  }

  await clickLabel(p, '儲存')
  await sleep(200)
  check(`${mode}: empty form blocked`, (await text(p)).includes('請至少填寫一項量測數值') && p.posts.length === 0)

  await fill(p, '體溫', '38.5')
  await sleep(150)
  let t = await text(p)
  check(`${mode}: typing 38.5 shows live critical hint + emergency call box before saving`,
    t.includes('體溫偏高（危急）') && t.includes('化療期間發燒可能是嚴重感染') && !!(await p.$('a[href^="tel:"]')))
  await fill(p, '心跳', 'abc')
  await fill(p, '收縮壓（上）', '118')
  await fill(p, '舒張壓（下）', '76')
  await fill(p, '血氧', '97')
  await clickLabel(p, '儲存')
  await sleep(200)
  check(`${mode}: invalid heart rate blocks save with field error`, (await text(p)).includes('請輸入數字') && p.posts.length === 0)
  await fill(p, '心跳', '125')
  await p.screenshot({ path: `${OUT}/vit-${mode}-form.png`, fullPage: true })
  await p.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find((x) => x.innerText.trim() === '儲存')
    b.click(); b.click()
  })
  await p.waitForFunction(() => document.body.innerText.includes('已記錄'), { timeout: 10000 })
  await idle(p)
  t = await text(p)
  check(`${mode}: result shows ${c.feverRule} alert (critical) + 心跳 125 alert, with call button`,
    t.includes('體溫 38.5°C') && t.includes('急診') && t.includes('心跳 125次/分') && !!(await p.$('a[href^="tel:"]')), t.slice(t.indexOf('已記錄'), t.indexOf('已記錄') + 160).replace(/\n/g, ' | '))
  if (mode === 'API') check('API: double tap → one POST with Idempotency-Key', p.posts.length === 1 && /^[0-9a-f-]{36}$/.test(p.posts[0]), p.posts.join(','))
  await p.screenshot({ path: `${OUT}/vit-${mode}-done.png`, fullPage: true })

  await clickLabel(p, '回首頁')
  await p.waitForFunction(() => location.pathname === '/patient')
  await idle(p); await sleep(300)
  t = await text(p)
  const badge = await p.evaluate(() => document.querySelector('nav [aria-label$="則未讀"]')?.innerText)
  check(`${mode}: dashboard latest vitals shows 38.5 flagged 危急 and notification badge grew`,
    /體溫\s*危急\s*38\.5/.test(t) && Number(badge) >= 2, `badge=${badge}`)

  console.log(`===== ${mode} (nurse) =====`)
  const n = await login(c.base, 'nurse01@demo.local', { width: 1440, height: 2400, deviceScaleFactor: 1 })
  await n.waitForFunction(() => document.body.innerText.includes('生命徵象異常'), { timeout: 10000 })
  await idle(n); await sleep(400)
  const section = 'section[aria-label="生命徵象異常"]'
  const expectTemp = mode === 'MOCK' ? '體溫 38.4°C' : '體溫 38.5°C'
  const expectRule = mode === 'MOCK' ? '疑似嗜中性白血球低下發燒' : '化療期間發燒'
  t = await n.$eval(section, (el) => el.innerText)
  check(`${mode}: nurse abnormal list shows the critical reading with unresolved ${expectRule}`,
    t.includes(expectTemp) && t.includes(expectRule) && t.includes('未處理'), t.slice(0, 150).replace(/\n/g, ' | '))
  await n.screenshot({ path: `${OUT}/vit-${mode}-nurse.png`, clip: { x: 0, y: 0, width: 1440, height: 1250 } })
  await n.evaluate((sel, rule) => {
    const box = [...document.querySelector(sel).querySelectorAll('li li')].find((li) => li.innerText.includes(rule))
    ;[...box.querySelectorAll('button')].find((b) => b.innerText.trim() === '處理').click()
  }, section, expectRule)
  await sleep(200)
  await n.evaluate((sel) => {
    const b = [...document.querySelector(sel).querySelectorAll('button')].find((x) => x.innerText.includes('已電話聯繫病人'))
    b.click()
  }, section)
  await n.evaluate((sel) => [...document.querySelector(sel).querySelectorAll('button')].find((b) => b.innerText.trim() === '標示為已處理').click(), section)
  await idle(n); await sleep(600)
  t = await n.$eval(section, (el) => el.innerText)
  check(`${mode}: resolved inline → shows 已由 測試護理師 林 處理 with note`,
    t.includes(`${expectRule}：已由 測試護理師 林 處理`) && t.includes('已電話聯繫病人'))
  const notesText = await n.$eval('section[aria-label="未處理通知"]', (el) => el.innerText)
  check(`${mode}: same alert disappears from 未處理通知 (shared workflow)`, !notesText.includes(expectRule))
}

await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
