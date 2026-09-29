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

async function nurseSession(base) {
  const ctx = await browser.createBrowserContext()
  const page = await ctx.newPage()
  await page.setViewport({ width: 1440, height: 2200, deviceScaleFactor: 1 })
  page.calls = []
  page.on('request', (r) => {
    if (['PATCH', 'POST'].includes(r.method()) && r.url().includes('/api/v1/')) page.calls.push(`${r.method()} ${new URL(r.url()).pathname}`)
  })
  await page.goto(base + '/login', { waitUntil: 'networkidle0' })
  await page.type('input[name=email]', 'nurse01@demo.local')
  await page.type('input[name=password]', 'Demo@1234')
  await page.click('button[type=submit]')
  await idle(page)
  await page.waitForFunction(() => document.body.innerText.includes('症狀審閱'), { timeout: 10000 })
  await idle(page)
  return page
}
const section = (label) => `section[aria-label="${label}"]`
const sectionText = (p, label) => p.$eval(section(label), (el) => el.innerText).catch(() => '')
// click a button with exact text inside a section (first match, or the one inside an element containing `within`)
const click = (p, label, text, within = null) =>
  p.evaluate((sel, text, within) => {
    const root = document.querySelector(sel)
    const scopes = within ? [...root.querySelectorAll('li, article')].filter((e) => e.innerText.includes(within)) : [root]
    for (const s of scopes) {
      const b = [...s.querySelectorAll('button')].find((x) => { const v = x.innerText.trim(); return v === text || v.startsWith(text + '（') })
      if (b) { b.click(); return true }
    }
    throw new Error(`button "${text}" not found in ${sel}${within ? ` / ${within}` : ''}`)
  }, section(label), text, within)

const CASES = {
  MOCK: { base: MOCK_URL, critical: '疑似嗜中性白血球低下發燒', warning: '疲倦程度偏高', expectOpen: 2, recordMark: '疲倦 8' },
  API: { base: WEB_URL, critical: '病人回報發燒或畏寒', warning: '疼痛程度偏高', expectOpen: 2, recordMark: '疼痛 8' },
}

for (const [mode, c] of Object.entries(CASES)) {
  console.log(`\n===== ${mode} =====`)
  const p = await nurseSession(c.base)
  const NOTES = '未處理通知'

  let t = await sectionText(p, NOTES)
  check(`${mode}: unresolved list shows ${c.expectOpen} alerts (critical + warning) with 未讀`,
    t.includes(c.critical) && t.includes(c.warning) && t.includes('未讀'), t.slice(0, 120).replace(/\n/g, ' | '))

  // mark the warning as read
  await click(p, NOTES, '標示已讀', c.warning)
  await idle(p); await sleep(300)
  t = await p.$eval(section(NOTES), (el) => [...el.querySelectorAll('li')].find((li) => li.innerText.includes(arguments[0] ?? '')))
    .catch(() => null)
  const warnItem = await p.evaluate((sel, w) => [...document.querySelector(sel).querySelectorAll('li')].find((li) => li.innerText.includes(w))?.innerText, section(NOTES), c.warning)
  check(`${mode}: 標示已讀 removes 未讀 from that alert`, warnItem && !warnItem.includes('未讀') && !warnItem.includes('標示已讀'))

  // resolve the critical alert with an empty note → error, then with a quick phrase
  await click(p, NOTES, '處理', c.critical)
  await sleep(200)
  await click(p, NOTES, '標示為已處理', c.critical)
  await sleep(200)
  check(`${mode}: resolving with an empty note shows an error`, (await sectionText(p, NOTES)).includes('請填寫處理說明'))
  await click(p, NOTES, '＋已電話聯繫病人', c.critical)
  await click(p, NOTES, '＋建議立即前往急診', c.critical)
  const noteValue = await p.$eval(`${section(NOTES)} textarea`, (el) => el.value)
  check(`${mode}: quick phrases compose the note`, noteValue === '已電話聯繫病人，建議立即前往急診', noteValue)
  await p.screenshot({ path: `${OUT}/rev-${mode}-resolve-form.png`, clip: { x: 0, y: 0, width: 1440, height: 1100 } })
  await click(p, NOTES, '標示為已處理', c.critical)
  await idle(p); await sleep(500)
  t = await sectionText(p, NOTES)
  check(`${mode}: resolved alert leaves the unresolved list`, !t.includes(c.critical) && t.includes(c.warning))
  await click(p, NOTES, '已處理')
  await idle(p); await sleep(400)
  t = await sectionText(p, NOTES)
  check(`${mode}: 已處理 tab shows who/when/note`,
    t.includes(c.critical) && t.includes('已處理：測試護理師 林') && t.includes('已電話聯繫病人，建議立即前往急診'))
  await click(p, NOTES, '未處理')
  await idle(p)

  // symptom review
  const REVIEW = '症狀審閱'
  t = await sectionText(p, REVIEW)
  check(`${mode}: review panel lists the report with high scores highlighted and open alert`,
    t.includes(c.recordMark) && t.includes('待審閱') && t.includes(c.warning), t.slice(0, 160).replace(/\n/g, ' | '))
  await click(p, REVIEW, '審閱', c.recordMark)
  await sleep(200)
  await click(p, REVIEW, '完成審閱', c.recordMark)
  await sleep(200)
  check(`${mode}: empty action note blocked`, (await sectionText(p, REVIEW)).includes('請填寫處置說明'))
  await p.evaluate((sel, mark) => {
    const card = [...document.querySelector(sel).querySelectorAll('article')].find((a) => a.innerText.includes(mark))
    const ta = card.querySelector('textarea')
    ta.value = '已電話衛教居家照護與止痛藥使用，明日電話追蹤'
    ta.dispatchEvent(new Event('input', { bubbles: true }))
    card.querySelector('details').open = true
    const radio = [...card.querySelectorAll('input[type=radio]')].find((r) => r.value === 'follow_up')
    radio.click()
    const select = card.querySelector('select')
    select.value = '2'
    select.dispatchEvent(new Event('change', { bubbles: true }))
  }, section(REVIEW), c.recordMark)
  const resolveBox = await p.evaluate((sel, mark) => {
    const card = [...document.querySelector(sel).querySelectorAll('article')].find((a) => a.innerText.includes(mark))
    return card.querySelector('input[type=checkbox]')?.checked
  }, section(REVIEW), c.recordMark)
  check(`${mode}: "also resolve alerts" is offered and on by default`, resolveBox === true)
  await p.screenshot({ path: `${OUT}/rev-${mode}-review-form.png`, fullPage: true })
  await click(p, REVIEW, '完成審閱', c.recordMark)
  await idle(p); await sleep(600)
  t = await sectionText(p, REVIEW)
  check(`${mode}: reviewed record leaves 待審閱`, !t.includes(c.recordMark))
  check(`${mode}: remaining alert resolved by the review → unresolved list empty`,
    (await sectionText(p, NOTES)).includes('目前沒有需要處理的警示'))
  await click(p, REVIEW, '已審閱')
  await idle(p); await sleep(400)
  t = await sectionText(p, REVIEW)
  check(`${mode}: 已審閱 tab shows reviewer, type and action note`,
    t.includes(c.recordMark) && t.includes('測試護理師 林') && t.includes('一般追蹤') && t.includes('已電話衛教居家照護與止痛藥使用，明日電話追蹤'))
  await p.screenshot({ path: `${OUT}/rev-${mode}-reviewed.png`, fullPage: true })
  if (mode === 'API') {
    check('API: calls made', p.calls.filter((x) => x.includes('/read')).length === 1 && p.calls.filter((x) => x.includes('/resolve')).length === 1
      && p.calls.filter((x) => x.includes('/review')).length === 1, p.calls.join(' | '))
  }
}

await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
