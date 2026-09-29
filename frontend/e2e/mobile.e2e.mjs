// 390px horizontal-overflow sweep over every patient / nurse page, both modes.
import puppeteer from 'puppeteer-core'
import { API_URL, CHROME, CHROME_ARGS, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const idle = (p) => p.waitForNetworkIdle({ idleTime: 400, timeout: 8000 }).catch(() => {})
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })
const phone = { width: 390, height: 844, deviceScaleFactor: 1 }
const errors = []

async function login(base, email) {
  const ctx = await browser.createBrowserContext()
  const page = await ctx.newPage()
  page.on('pageerror', (e) => errors.push(`${email}: ${e.message}`))
  await page.setViewport(phone)
  await page.goto(base + '/login', { waitUntil: 'networkidle0' })
  await page.type('input[name=email]', email)
  await page.type('input[name=password]', 'Demo@1234')
  await page.click('button[type=submit]')
  await idle(page)
  return page
}

/** Overflow = page wider than the viewport, or any visible element sticking out on the right. */
async function overflow(p) {
  return p.evaluate(() => {
    const W = document.documentElement.clientWidth
    const wide = [...document.querySelectorAll('body *')].filter((el) => {
      const r = el.getBoundingClientRect()
      if (!r.width || !r.height) return false
      // content inside an intentional horizontal scroller (e.g. wide tables) is fine
      for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
        const s = getComputedStyle(a)
        if ((s.overflowX === 'auto' || s.overflowX === 'scroll' || s.overflowX === 'hidden') && a.getBoundingClientRect().right <= W + 1) return false
        if (s.position === 'absolute' && s.clip !== 'auto') return false
      }
      return r.right > W + 1
    })
    return { scroll: document.documentElement.scrollWidth - W, elements: wide.slice(0, 3).map((e) => `${e.tagName}.${String(e.className).slice(0, 50)}`) }
  })
}
async function sweep(p, mode, name) {
  await idle(p); await sleep(300)
  const o = await overflow(p)
  check(`${mode} 390px: ${name}`, o.scroll <= 0 && !o.elements.length, o.scroll > 0 || o.elements.length ? JSON.stringify(o) : '')
  await p.screenshot({ path: `${OUT}/m390-${mode}-${name.replace(/[^\w]+/g, '_')}.png`, fullPage: true })
}
const clickText = (p, sel, text) => p.evaluate((sel, text) => {
  const el = [...document.querySelectorAll(sel)].find((x) => x.innerText.trim().includes(text))
  if (el) el.click()
  return !!el
}, sel, text)

for (const [mode, base, patientEmail] of [['MOCK', MOCK_URL, 'patient02@demo.local'], ['API', WEB_URL, 'patient01@demo.local']]) {
  console.log(`\n===== ${mode} =====`)
  if (mode === 'API') { // a risk alert so the Notification Center has something to open
    const auth = await (await fetch(`${API_URL}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: patientEmail, password: 'Demo@1234' }) })).json()
    await fetch(`${API_URL}/vital-signs`, { method: 'POST', headers: { 'Content-Type': 'application/json',
      Authorization: `Bearer ${auth.data.access_token}`, 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ patient_id: 'me', temperature_c: 38.6, heart_rate_bpm: 90 }) })
  }
  const p = await login(base, patientEmail)
  await p.waitForSelector('#w-lab-summary')
  await sweep(p, mode, 'Patient Dashboard (incl. Lab summary, notifications)')
  if (await clickText(p, '#w-symptom-quick-report button', '開始回報') || await clickText(p, '#w-symptom-quick-report button', '再次回報')) {
    await p.waitForSelector('#w-symptom-quick-report fieldset').catch(() => {})
    await sweep(p, mode, 'Symptom report form open')
  } else {
    check(`${mode} 390px: Symptom report form open`, false, 'start button not found')
  }
  await p.goto(base + '/patient/timeline', { waitUntil: 'networkidle0' })
  await p.waitForSelector('section[data-timeline] li[data-event-type]')
  await sweep(p, mode, 'Health Records → Patient Timeline')
  await p.$$eval('section[data-timeline] button[aria-expanded="false"]', (bs) => bs.forEach((b) => b.click()))
  await sweep(p, mode, 'Patient Timeline, all events expanded')
  await clickText(p, 'nav[aria-label="健康紀錄"] a', '記錄生命徵象')
  await p.waitForFunction(() => location.pathname === '/patient/vitals')
  await sweep(p, mode, 'Health Records → Vital entry')

  const n = await login(base, 'nurse01@demo.local')
  await n.waitForFunction(() => document.body.innerText.includes('我的個案'))
  await n.waitForSelector('section[aria-labelledby="lab-title"]')
  await sweep(n, mode, 'Nurse dashboard (incl. Lab panel, symptom review, timeline)')
  await clickText(n, 'section[aria-labelledby="lab-title"] button', '登錄檢驗')
  await sweep(n, mode, 'Nurse Lab entry form open')
  await n.goto(base + '/nurse/notifications', { waitUntil: 'networkidle0' })
  await n.waitForSelector('section[aria-label="通知列表"]')
  await sweep(n, mode, 'Notification Center list')
  const opened = await n.evaluate(() => {
    const b = document.querySelector('section[aria-label="通知列表"] li button[aria-label]')
    if (b) b.click()
    return !!b
  })
  check(`${mode}: Notification Center has an alert to open`, opened)
  if (opened) {
    await n.waitForSelector('section[aria-label="通知內容"] h2')
    await sweep(n, mode, 'Notification Center detail')
  }
}

check('no page errors', errors.length === 0, errors.join(' || ').slice(0, 300))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
