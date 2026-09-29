import puppeteer from 'puppeteer-core'
import { API_URL, CHROME, CHROME_ARGS, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })

async function session(base, email, viewport = { width: 390, height: 1400, deviceScaleFactor: 1 }) {
  const ctx = await browser.createBrowserContext()
  const page = await ctx.newPage()
  await page.setViewport(viewport)
  page.posts = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && r.url().includes('/symptoms/records')) page.posts.push(r.headers()['idempotency-key'])
  })
  await page.goto(base + '/login', { waitUntil: 'networkidle0' })
  await page.type('input[name=email]', email)
  await page.type('input[name=password]', 'Demo@1234')
  await page.click('button[type=submit]')
  await page.waitForNetworkIdle({ idleTime: 500, timeout: 5000 }).catch(() => {})
  return page
}
const text = (p) => p.evaluate(() => document.body.innerText)
const clickText = (p, label) =>
  p.evaluate((label) => {
    const el = [...document.querySelectorAll('button, label')].find((b) => b.innerText.trim() === label)
    if (!el) throw new Error(`no element "${label}"`)
    el.click()
  }, label)
// Pick an answer inside the fieldset whose legend contains `question`.
const answer = (p, question, value) =>
  p.evaluate((question, value) => {
    const fs = [...document.querySelectorAll('fieldset')].find((f) => f.querySelector('legend').innerText.includes(question))
    const label = [...fs.querySelectorAll('label')].find((l) => l.innerText.trim() === String(value))
    label.click()
  }, question, value)

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const p = await session(base, 'patient01@demo.local')
  await p.waitForFunction(() => document.body.innerText.includes('今天的症狀'))
  const before = await text(p)
  check(`${mode}: widget shows fever reminder tip in idle state`, before.includes('體溫 38°C 以上或畏寒發抖'))

  await clickText(p, '開始回報')
  await p.waitForFunction(() => document.querySelectorAll('fieldset').length >= 4, { timeout: 8000 })
  const legends = await p.$$eval('fieldset legend', (ls) => ls.map((l) => l.innerText.trim()))
  check(`${mode}: form rendered from API definition (4 questions)`, legends.length === 4 && legends[3].includes('發燒'), legends.join(' | '))

  await clickText(p, '送出回報')
  await sleep(200)
  check(`${mode}: empty submit blocked, shows 4 missing`, (await text(p)).includes('還有 4 題沒有回答') && p.posts.length === 0)

  await answer(p, '疼痛', 8)
  await answer(p, '噁心', 3)
  await answer(p, '疲倦', 4)
  await answer(p, '發燒', '有')
  await sleep(200)
  check(`${mode}: answering fever=有 shows emergency reminder with call button before submit`,
    (await text(p)).includes('化療期間發燒可能是嚴重感染') && (await p.$('a[href^="tel:"]')) !== null)
  await p.screenshot({ path: `${OUT}/sym-${mode}-form.png`, fullPage: true })

  // double-tap submit: must produce one record
  await p.evaluate(() => {
    const b = [...document.querySelectorAll('button')].find((x) => x.innerText.trim() === '送出回報')
    b.click(); b.click()
  })
  await p.waitForFunction(() => document.body.innerText.includes('已送出，謝謝您'), { timeout: 10000 })
  await p.waitForNetworkIdle({ idleTime: 600, timeout: 5000 }).catch(() => {})
  const done = await text(p)
  check(`${mode}: result shows critical fever alert and pain warning`,
    done.includes('發燒或畏寒') && done.includes('急診') && done.includes('疼痛程度偏高（8 分）'))
  {
    check(`${mode}: double tap sent one request with an Idempotency-Key`, mode === 'MOCK' ? p.posts.length === 0 : p.posts.length === 1 && /^[0-9a-f-]{36}$/.test(p.posts[0]), p.posts.join(','))
  }
  check(`${mode}: dashboard refreshed — critical alert banner at top`,
    await p.evaluate(() => !!document.querySelector('section[role=alert]')))
  await p.screenshot({ path: `${OUT}/sym-${mode}-done.png`, fullPage: true })

  await clickText(p, '完成')
  await sleep(300)
  const after = await text(p)
  check(`${mode}: widget now shows 「今天已回報」`, after.includes('今天已回報，謝謝您'))
  const unread = await p.evaluate(() => document.querySelector('nav [aria-label$="則未讀"]')?.innerText)
  check(`${mode}: notification badge increased`, Number(unread) >= 3, `badge=${unread}`)
  const todayPain = await p.evaluate(() => {
    const legend = [...document.querySelectorAll('#w-symptom-trend ul li')].find((li) => li.innerText.startsWith('疼痛'))
    return legend?.innerText
  })
  check(`${mode}: symptom trend legend shows today's pain 8`, /疼痛\s*8/.test(todayPain ?? ''), todayPain)

  // nurse sees it (API mode only: mock data lives in each browser session)
  if (mode === 'MOCK') continue
  const n = await session(base, 'nurse01@demo.local', { width: 1440, height: 1600, deviceScaleFactor: 1 })
  await n.waitForFunction(() => document.body.innerText.includes('我的個案'))
  await n.goto(base + '/nurse', { waitUntil: 'networkidle0' })
  await sleep(500)
  const nt = await text(n)
  check(`${mode}: nurse caseload shows P00001 at high risk with fever alert`,
    nt.includes('高風險') && nt.includes('病人回報發燒或畏寒'))
  await n.screenshot({ path: `${OUT}/sym-${mode}-nurse.png` })
}

await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
