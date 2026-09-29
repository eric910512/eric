// Mock-mode timestamp regression: loads the real mock modules through Vite (resolves '@/'),
// freezes the clock at one instant, and checks the same guarantees as the backend tests.
import { pathToFileURL } from 'node:url'

import { FRONTEND_DIR as FRONTEND } from './lib/env.mjs'
const { createServer } = await import(pathToFileURL(`${FRONTEND}/node_modules/vite/dist/node/index.js`).href)
const results = []
const check = (label, cond, detail = '') => {
  results.push(!!cond)
  console.log(`[${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? `  → ${detail}` : ''}`)
}

const server = await createServer({ root: FRONTEND, logLevel: 'error', server: { middlewareMode: true, hmr: false }, appType: 'custom' })
const nr = await server.ssrLoadModule('/src/mock/nurseReview.js')
const tl = await server.ssrLoadModule('/src/mock/timeline.js')

const MS = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/
const P2 = 'b8c4d1e2-3f5a-4b6c-9d7e-8f9a0b1c2d3e'

// Freeze "now" (new Date() / Date.now()) at one instant; explicit dates still work.
const RealDate = Date
const FROZEN = RealDate.parse('2026-09-25T03:00:00.500Z')
globalThis.Date = class extends RealDate {
  constructor(...args) { super(...(args.length ? args : [FROZEN])) }
  static now() { return FROZEN }
}

check('seed data: every mock timestamp uses the ms format',
  nr.mockListNotifications({ status: 'all' }).data.every((n) => MS.test(n.created_at))
  && tl.mockTimeline(P2, { audience: 'staff', limit: 100 }).data.every((e) => MS.test(e.occurred_at)))

// ---- full lifecycle, every call reads the same instant
for (const a of ['acknowledge', 'start']) nr.mockTransition(13, a)
const d = nr.mockTransition(13, 'resolve', '同秒完成')
const times = ['acknowledged', 'started', 'resolved'].map((k) => d.handling[k].at)
check('mock step times: ms format, strictly increasing, same second',
  times.every((t) => MS.test(t)) && RealDate.parse(times[0]) < RealDate.parse(times[1]) && RealDate.parse(times[1]) < RealDate.parse(times[2])
  && times.every((t) => t.slice(0, 19) === times[0].slice(0, 19)), times.join(' < '))
check('mock step times match the backend rule (1 ms apart)', times.join() === ['2026-09-25T03:00:00.500Z', '2026-09-25T03:00:00.501Z', '2026-09-25T03:00:00.502Z'].join())

const staff = tl.mockTimeline(P2, { audience: 'staff', limit: 100 }).data
const steps = staff.filter((e) => e.event_type === 'NOTIFICATION_STATUS' && e.source_id === 13)
check('mock timeline keeps ACKNOWLEDGED, IN_PROGRESS, RESOLVED as three events, newest first',
  steps.map((e) => e.detail.status).join() === 'resolved,in_progress,acknowledged' && steps.map((e) => e.occurred_at).join() === [...times].reverse().join(),
  steps.map((e) => `${e.detail.status}@${e.occurred_at}`).join(' | '))
const patient = tl.mockTimeline(P2, { audience: 'patient', limit: 100 }).data
check('patient mock timeline: same order, patient wording',
  patient.filter((e) => e.event_type === 'NOTIFICATION_STATUS' && e.source_id === 13).map((e) => e.title).join() === '已處理完成,護理師正在處理,護理師已接手')
const order = staff.map((e) => e.occurred_at)
check('mock timeline sorted newest → oldest by instant', order.every((t, i) => i === 0 || RealDate.parse(order[i - 1]) >= RealDate.parse(t)))

// ---- acknowledge, then quick resolve at the same instant
nr.mockTransition(11, 'acknowledge')
nr.mockResolve(11, '快速處理')
const q = tl.mockTimeline(P2, { audience: 'staff', limit: 100 }).data.filter((e) => e.event_type === 'NOTIFICATION_STATUS' && e.source_id === 11)
check("quick resolve after an acknowledge in the same instant: 'acknowledged' kept, quick steps collapse to 'resolved'",
  q.map((e) => e.detail.status).join() === 'resolved,acknowledged', q.map((e) => `${e.detail.status}@${e.occurred_at}`).join(' | '))

// ---- seed rows resolved before the lifecycle (one shared timestamp) still show a single step
const seeded = tl.mockTimeline(P2, { audience: 'staff', limit: 100 }).data.filter((e) => e.event_type === 'NOTIFICATION_STATUS' && e.source_id === 7)
check('pre-lifecycle seed row (steps share one timestamp) → one resolved step', seeded.map((e) => e.detail.status).join() === 'resolved')

globalThis.Date = RealDate
await server.close()
console.log(`\n${results.filter(Boolean).length}/${results.length} checks passed`)
process.exit(results.every(Boolean) ? 0 : 1)
