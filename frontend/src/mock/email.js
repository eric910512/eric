/**
 * MockEmailService (VITE_USE_MOCK=true) — the mock-mode counterpart of the backend capture provider
 * (app/services/email/capture.py). Nothing is sent: messages are recorded in an outbox
 * ({ id, to, subject, text, purpose, status, at }) kept in this tab's sessionStorage, so an E2E run
 * that signs in as the patient and then the nurse in one tab can read the verification link and
 * the notification email (`mockEmailOutbox()` / sessionStorage `ccp.mock.email-outbox`).
 *
 * Same test hook as the capture provider: a notification email to an address whose local part
 * ends with `+fail` fails (PROVIDER_REJECTED); `+timeout` fails with TIMEOUT. Verification emails
 * to those addresses succeed. Same texts as app/services/email/templates.py (summary only: never
 * the notification's title or message).
 */
import { nowIso } from '@/mock/clock'

const OUTBOX_KEY = 'ccp.mock.email-outbox'
const SYSTEM = '化療照護'
const SECURITY_NOTE = '安全提醒：本系統不會在 Email 中要求您提供密碼或驗證碼，也不會附上檔案。如果您沒有使用本系統，請忽略這封信。此信件由系統自動發送，請勿直接回覆。'

function load() {
  try {
    return JSON.parse(sessionStorage.getItem(OUTBOX_KEY)) ?? []
  } catch {
    return []
  }
}
const outbox = load()
function save() {
  try {
    sessionStorage.setItem(OUTBOX_KEY, JSON.stringify(outbox))
  } catch {
    // storage unavailable: the outbox lives in memory only
  }
}

/** `j***@example.com` (= templates.mask_email). */
export function maskEmail(address) {
  if (!address || !address.includes('@')) return null
  const [local, domain] = [address.slice(0, address.indexOf('@')), address.slice(address.indexOf('@') + 1)]
  return `${local.slice(0, 1)}***@${domain}`
}

function failureMode(address, purpose) {
  if (purpose !== 'notification') return null
  const local = (address ?? '').split('@')[0].toLowerCase()
  if (local.endsWith('+fail')) return 'fail'
  if (local.endsWith('+timeout')) return 'timeout'
  return null
}

const localTime = (iso) => new Date(iso).toLocaleString('sv-SE', { timeZone: 'Asia/Taipei' }).slice(0, 16)

/** Always available in mock mode (like EMAIL_PROVIDER=capture). */
export const mockEmailAvailable = true

/** send(message) → { status: sent | failed, provider_message_id, error_code }. Never throws. */
export function mockSendEmail({ to, subject, text, purpose }) {
  const mode = failureMode(to, purpose)
  const status = mode ? 'failed' : 'sent'
  const id = `mock-email-${crypto.randomUUID()}`
  outbox.push({ id, to, subject, text, purpose, status, at: nowIso() })
  save()
  if (mode === 'timeout') return { status: 'failed', provider_message_id: null, error_code: 'TIMEOUT' }
  if (mode === 'fail') return { status: 'failed', provider_message_id: null, error_code: 'PROVIDER_REJECTED' }
  return { status: 'sent', provider_message_id: id, error_code: null }
}

export function verificationEmail(to, link, hours, now) {
  return {
    to, purpose: 'verification', subject: `【${SYSTEM}】請驗證您的通知 Email`,
    text: [SYSTEM, '', '您好：', `請在 ${hours} 小時內開啟下列連結，完成通知 Email 的驗證（需要先登入${SYSTEM}）：`, '', link, '',
      `寄送時間：${localTime(now)}`, '', SECURITY_NOTE].join('\n'),
  }
}

export function notificationEmail(to, sentAt) {
  return {
    to, purpose: 'notification', subject: `【${SYSTEM}】您有一則新的通知`,
    text: [SYSTEM, '', '您有一則來自護理團隊的新通知。', `發送時間：${localTime(sentAt)}`, '', `請登入${SYSTEM} App / 網頁查看詳細內容：`,
      `${location.origin}/`, '', '如不想再收到 Email 通知，可以在「我的」頁面關閉「接收 Email 通知」。', '', SECURITY_NOTE].join('\n'),
  }
}

/** Every message recorded so far (E2E checks). */
export function mockEmailOutbox() {
  return structuredClone(outbox)
}
