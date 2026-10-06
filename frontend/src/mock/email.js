/**
 * MockEmailService (VITE_USE_MOCK=true) — the mock-mode counterpart of the backend capture provider
 * (app/services/email/capture.py). Nothing is sent: messages are recorded in an outbox
 * ({ id, to, subject, text, purpose, status, at }) kept in this tab's sessionStorage, so an E2E run
 * that signs in as the patient and then the nurse in one tab can read the verification link and
 * the notification email (`mockEmailOutbox()` / sessionStorage `ccp.mock.email-outbox`).
 *
 * Same test hook as the capture provider: a notification email to an address whose local part
 * ends with `+fail` fails (PROVIDER_REJECTED); `+timeout` fails with TIMEOUT. Verification emails
 * to those addresses succeed. Same texts and HTML as app/services/email/templates.py: `summary` (the
 * nurse's title only for non-sensitive topics, never the content) or `full` (title + content); the mode
 * and title are decided by the caller with the content policy (`@/utils/emailPolicy`).
 */
import { nowIso } from '@/mock/clock'

const OUTBOX_KEY = 'ccp.mock.email-outbox'
const SYSTEM = '癌症照護系統'
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
export function mockSendEmail({ to, subject, text, purpose, html = null }) {
  const mode = failureMode(to, purpose)
  const status = mode ? 'failed' : 'sent'
  const id = `mock-email-${crypto.randomUUID()}`
  outbox.push({ id, to, subject, text, purpose, html, status, at: nowIso() })
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

const escapeHtml = (v) => String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#x27;')
/** Escaped text with `://` broken by a zero-width space so mail clients do not auto-link nurse URLs. */
const defang = (escaped) => escaped.replaceAll('://', ':/\u200b/')
const subjectText = (value, limit = 120) => {
  const line = String(value).split(/\s+/).filter(Boolean).join(' ')
  return line.length <= limit ? line : `${line.slice(0, limit - 1)}…`
}

function htmlBody(paragraphs, label, url) {
  const parts = paragraphs.map((item) => (Array.isArray(item)
    ? `<p style="margin:0 0 16px"><strong>${escapeHtml(item[0])}</strong><br>${String(item[1]).split('\n').map((l) => defang(escapeHtml(l))).join('<br>')}</p>`
    : `<p style="margin:0 0 16px">${defang(escapeHtml(item))}</p>`))
  const button = `<p style="margin:24px 0"><a href="${escapeHtml(url)}" style="display:inline-block;padding:12px 24px;border-radius:999px;background:#1f5f7a;color:#ffffff;text-decoration:none;font-weight:bold">${escapeHtml(label)}</a></p>`
  const note = `<p style="margin:24px 0 0;font-size:13px;color:#666">${escapeHtml(SECURITY_NOTE)}</p>`
  return `<div style="font-family:sans-serif;font-size:15px;line-height:1.6;color:#222">${parts.join('')}${button}${note}</div>`
}

/**
 * `summary` or `full` email of a nurse-sent notification (= templates.notification_email). `mode` is the
 * mode the policy allows; for a summary `title` is the title the email may show (generic for sensitive topics).
 */
export function notificationEmail(to, { mode, title, message, sentAt }) {
  const when = localTime(sentAt)
  const url = `${location.origin}/patient/notifications`
  const login = `登入${SYSTEM}`
  const paragraphs = mode === 'full'
    ? ['您好，', '您有一則來自護理團隊的新通知。', ['標題：', title], ['內容：', message], ['發送時間：', when]]
    : ['您好，', '您有一則來自護理團隊的新通知。', ['通知標題：', title], ['發送時間：', when], `為保護您的醫療資訊，完整內容請登入${SYSTEM}查看。`]
  const lines = paragraphs.flatMap((item) => (Array.isArray(item) ? [item[0], ...String(item[1]).split('\n'), ''] : [item, '']))
  return {
    to, purpose: 'notification',
    subject: mode === 'full' ? subjectText(`${SYSTEM}｜${title}`) : `${SYSTEM}｜您有一則新通知`,
    text: [...lines, `${login}：`, url, '', SECURITY_NOTE].join('\n'),
    html: htmlBody(paragraphs, login, url),
  }
}

/** Every message recorded so far (E2E checks). */
export function mockEmailOutbox() {
  return structuredClone(outbox)
}
