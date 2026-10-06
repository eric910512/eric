/**
 * Email content policy of nurse-sent notifications — display copy of
 * backend/app/modules/notification/email_policy.py. The backend decides (a sensitive topic asking for
 * `full` is downgraded to `summary` there, whatever the client sends); the UI only offers what is allowed.
 */
export const EMAIL_CATEGORIES = [
  { value: 'schedule', label: '行程與報到', full: true },
  { value: 'preparation', label: '就診準備', full: true },
  { value: 'medication', label: '用藥與治療', full: false },
  { value: 'symptom_followup', label: '症狀與照護追蹤', full: false },
  { value: 'clinical_other', label: '其他醫療相關', full: false },
]
export const DEFAULT_CATEGORY = 'clinical_other' // also what the backend uses when no topic is sent
export const DEFAULT_EMAIL_MODE = 'summary'
export const EMAIL_MODES = { none: '不寄 Email', summary: '寄送摘要', full: '寄送標題與內容' }
export const GENERIC_EMAIL_TITLE = '您有一則來自護理團隊的醫療照護通知'
export const SENSITIVE_NOTICE = '此類通知可能包含敏感醫療資訊，為保護病人隱私，Email 僅提供通知摘要，完整內容請病人登入 App 查看。'

export const categoryLabel = (value) => EMAIL_CATEGORIES.find((c) => c.value === value)?.label ?? value
/** Unknown / missing topics are sensitive (fail closed), like the backend. */
export const allowsFull = (category) => !!EMAIL_CATEGORIES.find((c) => c.value === (category || DEFAULT_CATEGORY))?.full
/** { category, requested, mode } — the same resolution as email_policy.resolve(). */
export function resolveEmailMode(category, requested) {
  const cat = category || DEFAULT_CATEGORY
  const req = requested || DEFAULT_EMAIL_MODE
  return { category: cat, requested: req, mode: req === 'full' && !allowsFull(cat) ? 'summary' : req }
}
