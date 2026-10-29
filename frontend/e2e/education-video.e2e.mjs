// 衛教影片 on the patient home (2026-10): a static MP4 from frontend/public/videos/, right under the
// daily report entry. Both modes, 390 px: visible after sign-in, position, <video> attributes (controls,
// playsinline, preload=metadata, no autoplay), source = the public file (not bundled), the file is
// served (incl. byte ranges), metadata loads and it plays, still plays after a reload, no overflow.
import fs from 'node:fs'
import path from 'node:path'
import puppeteer from 'puppeteer-core'
import { CHROME, CHROME_ARGS, FRONTEND_DIR, MOCK_URL, OUT, WEB_URL } from './lib/env.mjs'

const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}
const idle = (p) => p.waitForNetworkIdle({ idleTime: 400, timeout: 8000 }).catch(() => {})
const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: CHROME_ARGS })
const phone = { width: 390, height: 844, deviceScaleFactor: 1 }
const SRC = '/videos/radiation-self-care.mp4'
const FILE_SIZE = fs.statSync(path.join(FRONTEND_DIR, 'public', SRC)).size
const VIDEO = '[data-education-video] video'
const errors = []

async function signIn(base) {
  const ctx = await browser.createBrowserContext()
  const p = await ctx.newPage()
  p.on('pageerror', (e) => errors.push(e.message))
  await p.setViewport(phone)
  await p.goto(base + '/login', { waitUntil: 'networkidle0' })
  await p.type('input[name=email]', 'patient01@demo.local')
  await p.type('input[name=password]', 'Demo@1234')
  await p.click('button[type=submit]')
  await idle(p)
  return p
}
const noOverflow = (p) => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
// metadata loaded → intrinsic size + duration (readyState ≥ 1)
const metadata = (p) => p.waitForFunction((sel) => {
  const v = document.querySelector(sel)
  return v && v.readyState >= 1 && v.videoWidth > 0 && v.duration > 0
}, { timeout: 20000 }, VIDEO).then(() => p.$eval(VIDEO, (v) => ({ w: v.videoWidth, h: v.videoHeight, duration: Math.round(v.duration) })), () => null)
// play (muted, so headless Chrome allows it) until the clock moves, then pause again
const plays = (p) => p.$eval(VIDEO, async (v) => {
  v.muted = true
  try { await v.play() } catch (e) { return `play() rejected: ${e.message}` }
  const t0 = v.currentTime
  await new Promise((r) => setTimeout(r, 1200))
  const moved = v.currentTime > t0
  v.pause(); v.currentTime = 0
  return moved ? true : `currentTime stayed ${v.currentTime}`
})

for (const [mode, base] of [['MOCK', MOCK_URL], ['API', WEB_URL]]) {
  console.log(`\n===== ${mode} =====`)
  const p = await signIn(base)
  check(`${mode}: signed in → /patient`, await p.waitForFunction(() => location.pathname === '/patient', { timeout: 10000 }).then(() => true, () => false))
  check(`${mode}: 衛教影片 is on the home page right after sign-in`, await p.waitForSelector('[data-education-video]', { timeout: 10000 }).then(() => true, () => false))
  const frame = await p.$eval('#education-video', (e) => e.innerText).catch(() => '')
  check(`${mode}: titled 衛教影片 / 放射線治療期間的自我照護`, frame.includes('衛教影片') && frame.includes('放射線治療期間的自我照護'), frame.replace(/\n/g, ' | ').slice(0, 80))

  const pos = await p.evaluate(() => {
    const blocks = [...document.querySelectorAll('main > [id]')].map((e) => e.id)
    return { blocks, at: blocks.indexOf('education-video') }
  })
  check(`${mode}: position — daily report entry → 衛教影片 → 病人基本資料`,
    pos.at === 1 && pos.blocks[0] === 'w-daily-report' && pos.blocks[2] === 'w-patient-summary', pos.blocks.slice(0, 5).join(' > '))

  const v = await p.$eval(VIDEO, (v) => ({
    controls: v.hasAttribute('controls'), playsinline: v.hasAttribute('playsinline'), preload: v.getAttribute('preload'),
    autoplay: v.hasAttribute('autoplay') || v.autoplay, paused: v.paused,
    src: v.querySelector('source')?.getAttribute('src'), type: v.querySelector('source')?.getAttribute('type'), current: v.currentSrc,
  })).catch(() => null)
  check(`${mode}: <video controls playsinline preload="metadata">, no autoplay, not playing`,
    v && v.controls && v.playsinline && v.preload === 'metadata' && !v.autoplay && v.paused, JSON.stringify(v))
  check(`${mode}: source is the public file ${SRC} (video/mp4), not a bundled asset`,
    v?.src === SRC && v.type === 'video/mp4' && new URL(v.current).pathname === SRC, `${v?.src} | ${v?.current}`)

  // the static file itself: full GET size + a byte range (what the player uses to seek)
  const served = await p.evaluate(async (src) => {
    const head = await fetch(src, { method: 'HEAD' })
    const part = await fetch(src, { headers: { Range: 'bytes=0-1023' } })
    return { status: head.status, type: head.headers.get('content-type'), length: Number(head.headers.get('content-length')),
      rangeStatus: part.status, rangeBytes: (await part.arrayBuffer()).byteLength }
  }, SRC)
  check(`${mode}: ${SRC} is served as video/mp4, full size, with byte ranges`,
    served.status === 200 && served.type?.startsWith('video/mp4') && served.length === FILE_SIZE && served.rangeStatus === 206 && served.rangeBytes === 1024,
    JSON.stringify(served))

  const meta = await metadata(p)
  check(`${mode}: metadata loads (size + duration)`, !!meta, JSON.stringify(meta))
  const box = await p.$eval(VIDEO, (v) => {
    const r = v.getBoundingClientRect(), c = v.parentElement.getBoundingClientRect()
    return { left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width), height: Math.round(r.height), container: Math.round(c.width), vw: innerWidth }
  })
  const ratioOk = meta && Math.abs(box.height - box.width * meta.h / meta.w) <= 2
  check(`${mode} 390 px: player fills the card width, height follows the video's aspect ratio`,
    box.width === box.container && box.left >= 0 && box.right <= box.vw && ratioOk, JSON.stringify(box))
  check(`${mode} 390 px: home has no horizontal overflow`, await noOverflow(p))
  const played = await plays(p)
  check(`${mode}: the video plays`, played === true, String(played))
  await p.screenshot({ path: `${OUT}/edu-video-${mode}-home.png`, clip: { x: 0, y: 0, width: 390, height: 844 } })

  // reload: same player, same file, still plays
  await p.reload({ waitUntil: 'networkidle0' })
  await p.waitForSelector(VIDEO, { timeout: 10000 }).catch(() => {})
  const after = await p.$eval(VIDEO, (v) => ({ src: v.querySelector('source')?.getAttribute('src'), autoplay: v.autoplay, paused: v.paused })).catch(() => null)
  const meta2 = await metadata(p)
  check(`${mode}: after a reload the video is still there (no autoplay) and loads`, after?.src === SRC && !after.autoplay && after.paused && !!meta2, JSON.stringify({ after, meta2 }))
  check(`${mode}: …and still plays`, (await plays(p)) === true)
  check(`${mode} 390 px: still no horizontal overflow after reload`, await noOverflow(p))
}

check('no page errors', errors.length === 0, errors.join(' || ').slice(0, 300))
await browser.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
