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
const phone = { width: 390, height: 1800, deviceScaleFactor: 1 }
const desk = { width: 1440, height: 2400, deviceScaleFactor: 1 }
const LAB = '#w-lab-summary'
const PANEL = 'section[aria-labelledby="lab-title"]'
const text = (p, sel) => p.$eval(sel, (el) => el.innerText).catch(() => '')
const noHScroll = (p) => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
const clickText = (p, sel, t) => p.evaluate((sel, t) => {
  const el = [...document.querySelectorAll(sel)].find((b) => b.innerText.trim().includes(t))
  if (!el) return false
  el.click(); return true
}, sel, t)
async function fill(p, code, value) {
  await p.evaluate((code, value) => {
    const el = document.querySelector(`input[name="lab-${code}"]`)
    el.value = value
    el.dispatchEvent(new Event('input', { bubbles: true }))
  }, code, String(value))
}
async function selectPatient(n, code) {
  // the caseload list renders after its API response, later than the page heading
  await n.waitForFunction((code) => [...document.querySelectorAll('section[aria-labelledby="caseload-title"] li button')].some((b) => b.innerText.includes(code)), { timeout: 15000 }, code)
  await n.evaluate((code) => [...document.querySelectorAll('section[aria-labelledby="caseload-title"] li button')].find((b) => b.innerText.includes(code)).click(), code)
  await idle(n); await sleep(300)
  await n.waitForSelector(PANEL)
  await n.waitForFunction((sel) => document.querySelector(sel)?.innerText.includes('次採檢'), {}, PANEL)
}

for (const MODE of ['MOCK', 'API']) {
  const base = MODE === 'MOCK' ? MOCK_URL : WEB_URL
  console.log(`\n===== ${MODE} =====`)

  // ---------------- patient: simplified results ----------------
  let p = await login(base, 'patient01@demo.local', phone)
  await p.waitForSelector(LAB)
  let t = await text(p, LAB)
  check(`${MODE}: patient sees 最近檢驗結果 with friendly names + 正常`, t.includes('最近檢驗結果') && t.includes('嗜中性白血球（抵抗力）')
    && t.includes('血色素') && t.includes('正常') && t.includes('都在正常範圍'), t.replace(/\n/g, ' | ').slice(0, 160))
  check(`${MODE}: patient view hides reference ranges / flags codes`, !/參考|LL|4\.0–10|1\.5–7\.5/.test(t))
  check(`${MODE}: widget placed after latest vitals`, await p.evaluate(() => {
    const ids = [...document.querySelectorAll('[id^="w-"]')].map((e) => e.id)
    return ids.indexOf('w-lab-summary') === ids.indexOf('w-latest-vitals') + 1
  }))
  check(`${MODE}: 390px no horizontal scroll`, await noHScroll(p))

  // ---------------- nurse: full data + entry ----------------
  const n = await login(base, 'nurse01@demo.local', desk)
  await n.waitForFunction(() => document.body.innerText.includes('我的個案'))
  await selectPatient(n, 'P00001')
  t = await text(n, PANEL)
  check(`${MODE}: nurse panel shows ref range, 危急 threshold, flag and unit`, t.includes('1.5–7.5') && t.includes('4.0–10.0') && t.includes('危急 ≤0.5') && t.includes('10³/µL')
    && t.includes('正常') && t.includes('3.8'), t.replace(/\n/g, ' | ').slice(0, 200))

  // validation: empty form, then an out-of-range value
  await clickText(n, `${PANEL} button`, '登錄檢驗')
  await n.waitForSelector('input[name="lab-ANC"]')
  await clickText(n, `${PANEL} button`, '儲存檢驗結果')
  await sleep(200)
  check(`${MODE}: empty entry → 請至少填寫一項檢驗值`, (await text(n, PANEL)).includes('請至少填寫一項檢驗值'))
  await fill(n, 'HGB', 45)
  await clickText(n, `${PANEL} button`, '儲存檢驗結果')
  await sleep(200)
  check(`${MODE}: Hb 45 → inline range error, nothing sent`, (await text(n, PANEL)).includes('請確認數值（1–25）'))
  await fill(n, 'HGB', 9.8)
  check(`${MODE}: typing clears the error; live flag preview shows 偏低`, await n.evaluate((sel) => {
    const t = document.querySelector(sel).innerText
    return !t.includes('請確認數值') && t.includes('偏低')
  }, PANEL))
  await fill(n, 'WBC', 1.9); await fill(n, 'ANC', 0.4); await fill(n, 'PLT', 88)
  check(`${MODE}: ANC 0.4 preview → 嚴重偏低`, (await text(n, PANEL)).includes('嚴重偏低'))
  await n.screenshot({ path: `${OUT}/lab-${MODE}-nurse-form.png`, clip: await n.$eval(PANEL, (el) => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y + scrollY, width: r.width, height: r.height } }) })
  // double click → one submission
  await n.evaluate((sel) => { const b = [...document.querySelectorAll(`${sel} button`)].find((x) => x.innerText.includes('儲存檢驗結果')); b.click(); b.click() }, PANEL)
  await n.waitForFunction((sel) => document.querySelector(sel)?.innerText.includes('已登錄'), { timeout: 8000 }, PANEL)
  await idle(n); await sleep(400)
  t = await text(n, PANEL)
  check(`${MODE}: result banner: 4 items + 危急警示 ANC notified`, t.includes('已登錄 4 項檢驗') && t.includes('危急警示：嗜中性白血球（抵抗力）') && t.includes('已通知病人與護理團隊'),
    t.split('\n').slice(0, 6).join(' | '))
  check(`${MODE}: table now shows ANC 0.4 嚴重偏低 + open alert row`, t.includes('0.4') && t.includes('嚴重偏低') && t.includes('嗜中性白血球嚴重低下：尚未處理'))
  const risk = await text(n, 'section[aria-label="風險判斷"]')
  check(`${MODE}: risk engine → 高風險 with ANC reason`, risk.includes('高風險') && /ANC 0\.4 10³\/µL（嚴重偏低）/.test(risk), risk.replace(/\n/g, ' | '))
  const alerts = await n.evaluate(() => document.body.innerText.includes('這位病人的未處理警示') && document.body.innerText.includes('嗜中性白血球嚴重低下'))
  check(`${MODE}: alert appears in the patient's unresolved alert list`, alerts)
  const caseRow = await n.evaluate(() => [...document.querySelectorAll('section[aria-labelledby="caseload-title"] li button')].find((b) => b.innerText.includes('P00001')).innerText)
  check(`${MODE}: caseload row → 危急警示 + lab reason`, caseRow.includes('危急警示') && caseRow.includes('ANC 0.4'), caseRow.replace(/\n/g, ' | ').slice(0, 160))
  await clickText(n, `${PANEL} button`, '歷次檢驗')
  await sleep(200)
  t = await text(n, '#lab-history')
  check(`${MODE}: history table: 2 panels, flags as superscripts`, (t.match(/\n/g) ?? []).length >= 2 && t.includes('LL') && t.includes('3.8'), t.replace(/\n/g, ' | ').slice(0, 200))
  await n.screenshot({ path: `${OUT}/lab-${MODE}-nurse.png`, clip: await n.$eval(PANEL, (el) => { const r = el.getBoundingClientRect(); return { x: r.x, y: r.y + scrollY, width: r.width, height: r.height } }) })

  // resolve the alert from the alert list → panel shows 已處理
  const resolved = await n.evaluate(() => {
    const card = [...document.querySelectorAll('li')].find((el) => el.innerText.includes('嗜中性白血球嚴重低下') && [...el.querySelectorAll('button')].some((b) => b.innerText.trim() === '處理'))
    if (!card) return false
    ;[...card.querySelectorAll('button')].find((b) => b.innerText.trim() === '處理').click()
    card.setAttribute('data-e2e', 'lab-alert')
    return true
  })
  if (resolved) {
    await n.waitForSelector('li[data-e2e="lab-alert"] textarea')
    await n.evaluate(() => {
      const ta = document.querySelector('li[data-e2e="lab-alert"] textarea')
      ta.value = '已電話聯絡，安排回診評估'
      ta.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await n.evaluate(() => [...document.querySelectorAll('li[data-e2e="lab-alert"] button')].find((b) => /標示為已處理/.test(b.innerText)).click())
    await idle(n); await sleep(800)
  }
  t = await text(n, PANEL)
  check(`${MODE}: resolving via alert list → lab panel shows 已由…處理`, resolved && t.includes('嗜中性白血球嚴重低下：已由') && t.includes('已電話聯絡'), t.split('\n').filter((l) => l.includes('嗜中性')).join(' | '))

  // nurse at 390px: panel scrolls inside itself, page does not
  await n.setViewport(phone); await sleep(300)
  check(`${MODE}: nurse panel at 390px → no page horizontal scroll`, await noHScroll(n))
  await n.setViewport(desk)

  // ---------------- patient sees the new simplified result ----------------
  await p.reload({ waitUntil: 'networkidle0' })
  if (MODE === 'MOCK') {
    // mock data is per browser session: re-check through the same tab is not possible, so use a nurse→patient mirror check
  }
  p = MODE === 'MOCK' ? p : await login(base, 'patient01@demo.local', phone)
  await p.waitForSelector(LAB)
  t = await text(p, LAB)
  if (MODE === 'API') {
    check(`${MODE}: patient summary after entry → 過低 + infection advice, critical tone`, t.includes('過低') && t.includes('一旦發燒請立即就醫') && t.includes('需要特別注意')
      && await p.$eval(LAB, (el) => el.querySelector('section')?.className.includes('border-critical') || el.className.includes('border-critical') || !!el.closest('.border-critical') || !!el.querySelector('.border-critical\\/60')), t.replace(/\n/g, ' | ').slice(0, 220))
    check(`${MODE}: patient notification shows plain-language ANC alert`, await p.evaluate(() => document.body.innerText.includes('嗜中性白血球（抵抗力）為 0.4')))
    await p.screenshot({ path: `${OUT}/lab-${MODE}-patient.png`, clip: await p.$eval(LAB, (el) => { const r = el.getBoundingClientRect(); return { x: 0, y: r.y + scrollY - 8, width: 390, height: r.height + 16 } }) })
  } else {
    await p.screenshot({ path: `${OUT}/lab-${MODE}-patient.png`, clip: await p.$eval(LAB, (el) => { const r = el.getBoundingClientRect(); return { x: 0, y: r.y + scrollY - 8, width: 390, height: r.height + 16 } }) })
    // mock: same-session check on P00002 via nurse context (nadir CBC, below range, no alert rule)
    await selectPatient(n, 'P00002')
    t = await text(n, PANEL)
    check(`${MODE}: P00002 nadir CBC → ANC 1.2 偏低, no alert row; risk reason 血小板/ANC 偏低`, t.includes('1.2') && t.includes('偏低') && !t.includes('尚未處理')
      && (await text(n, 'section[aria-label="風險判斷"]')).includes('ANC 1.2 10³/µL（偏低）'))
  }
}

// mock patient with an abnormal result: P00002 (nadir CBC)
console.log('\n===== MOCK patient02 =====')
const p2 = await login(MOCK_URL, 'patient02@demo.local', phone)
await p2.waitForSelector(LAB)
const t2 = await text(p2, LAB)
check('MOCK: patient02 sees 偏低 with daily-life advice, no numbers for ranges', t2.includes('偏低') && t2.includes('勤洗手') && t2.includes('軟毛牙刷') && t2.includes('有部分數值不在正常範圍'), t2.replace(/\n/g, ' | ').slice(0, 200))
await p2.screenshot({ path: `${OUT}/lab-MOCK-patient02.png`, clip: await p2.$eval(LAB, (el) => { const r = el.getBoundingClientRect(); return { x: 0, y: r.y + scrollY - 8, width: 390, height: r.height + 16 } }) })

check('no page errors / console errors', errors.length === 0, errors.join(' || ').slice(0, 300))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
