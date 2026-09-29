const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

function parts(iso, timeZone) {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  })
  return Object.fromEntries(fmt.formatToParts(new Date(iso)).map((p) => [p.type, p.value]))
}

/** "14:00" in the patient's timezone. */
export function formatTime(iso, timeZone = 'Asia/Taipei') {
  if (!iso) return ''
  const p = parts(iso, timeZone)
  return `${p.hour}:${p.minute}`
}

/** Local calendar date "2026-09-24" for an ISO datetime. */
export function localDate(iso, timeZone = 'Asia/Taipei') {
  const p = parts(iso, timeZone)
  return `${p.year}-${p.month}-${p.day}`
}

/** "9月24日（四）" for a "2026-09-24" date string. */
export function formatDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(`${dateStr}T00:00:00`)
  return `${d.getMonth() + 1}月${d.getDate()}日（${WEEKDAYS[d.getDay()]}）`
}

/** "9/24" */
export function formatShortDate(dateStr) {
  if (!dateStr) return ''
  const [, m, d] = dateStr.split('-')
  return `${Number(m)}/${Number(d)}`
}

/** "今天 14:00" / "昨天 20:30" / "9/21 11:00", relative to `today` (local date string). */
export function formatWhen(iso, today, timeZone = 'Asia/Taipei') {
  if (!iso) return ''
  const day = localDate(iso, timeZone)
  const diff = Math.round((new Date(today) - new Date(day)) / 86400000)
  const label = diff === 0 ? '今天' : diff === 1 ? '昨天' : formatShortDate(day)
  return `${label} ${formatTime(iso, timeZone)}`
}

export function addDays(dateStr, n) {
  const d = new Date(`${dateStr}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

export function daysBetween(fromStr, toStr) {
  return Math.round((new Date(`${toStr}T00:00:00Z`) - new Date(`${fromStr}T00:00:00Z`)) / 86400000)
}
