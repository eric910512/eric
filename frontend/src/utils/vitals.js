/**
 * Vital-sign field metadata — mirrors backend app/services/vitals.py (input ranges are
 * validated again on the server; thresholds come from GET /vital-signs/reference-ranges).
 */
export const VITAL_FIELDS = {
  temperature_c: { label: '體溫', unit: '°C', min: 30, max: 45, decimals: 1, placeholder: '36.5' },
  heart_rate_bpm: { label: '心跳', unit: '次/分', min: 20, max: 250, decimals: 0, placeholder: '80' },
  systolic_bp_mmhg: { label: '收縮壓', unit: 'mmHg', min: 50, max: 260, decimals: 0, placeholder: '120' },
  diastolic_bp_mmhg: { label: '舒張壓', unit: 'mmHg', min: 30, max: 160, decimals: 0, placeholder: '80' },
  spo2_pct: { label: '血氧', unit: '%', min: 50, max: 100, decimals: 0, placeholder: '98' },
  respiratory_rate: { label: '呼吸', unit: '次/分', min: 4, max: 60, decimals: 0, placeholder: '16' },
  weight_kg: { label: '體重', unit: 'kg', min: 20, max: 300, decimals: 1, placeholder: '60.0' },
}

export const TEMPERATURE_SITES = { ear: '耳溫', forehead: '額溫', oral: '口溫', axillary: '腋溫' }
export const BP_SITES = { left_arm: '左手', right_arm: '右手', leg: '腳' }

const DIRECTION = {
  temperature_c: ['偏低', '偏高'],
  heart_rate_bpm: ['偏慢', '偏快'],
  systolic_bp_mmhg: ['偏低', '偏高'],
  diastolic_bp_mmhg: ['偏低', '偏高'],
  respiratory_rate: ['偏慢', '偏快'],
  spo2_pct: ['偏低', '偏高'],
}

/** 'critical' | 'warning' | null — same rules as the backend (high: >=, low: <=). */
export function vitalFlag(field, value, ranges) {
  const r = ranges?.[field]
  if (value == null || Number.isNaN(value) || !r) return null
  if (r.critical_high != null && value >= r.critical_high) return 'critical'
  if (r.critical_low != null && value <= r.critical_low) return 'critical'
  if (r.warning_high != null && value >= r.warning_high) return 'warning'
  if (r.warning_low != null && value <= r.warning_low) return 'warning'
  return null
}

export function vitalHint(field, value, ranges) {
  const level = vitalFlag(field, value, ranges)
  if (!level) return null
  const r = ranges[field]
  const highs = [r.warning_high, r.critical_high].filter((v) => v != null)
  const [low, high] = DIRECTION[field] ?? ['偏低', '偏高']
  const dir = highs.length && value >= Math.min(...highs) ? high : low
  return { level, text: `${VITAL_FIELDS[field].label}${dir}` }
}

/** Parse a text input into a number, or null when empty. NaN signals invalid input. */
export function parseNumber(text) {
  const t = String(text ?? '').trim()
  if (!t) return null
  return /^-?\d+(\.\d+)?$/.test(t) ? Number(t) : Number.NaN
}

export function validateVital(field, value) {
  const meta = VITAL_FIELDS[field]
  if (value === null) return null
  if (Number.isNaN(value)) return '請輸入數字'
  if (meta.decimals === 0 && !Number.isInteger(value)) return '請輸入整數'
  if (value < meta.min || value > meta.max) return `請輸入 ${meta.min}–${meta.max} 之間的數值`
  return null
}
