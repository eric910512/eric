/**
 * Mock 皮膚照片 (Phase 1b, VITE_USE_MOCK=true) — same shapes and rules as backend app/modules/photo:
 * versioned draft consent, pending → attached (with the report), discard before sending only, access
 * (own / assigned nurse / admin 403), history with dates and body sites. The images are the JPEG data URLs
 * the browser prepared (utils/imagePrep.js); mock handlers are synchronous.
 */
import { nowIso } from '@/mock/clock'
import { mockListRecordsStore } from '@/mock/nurseReview'
import { MockApiError, mockAccessPatient, mockCurrentUser } from '@/mock/patients'

const KEY = 'ccp.mock.photos'
export const PHOTO_CONSENT_VERSION = 'skin-photo-2026-11-draft-1'
const CONSENT = {
  version: PHOTO_CONSENT_VERSION,
  title: '皮膚照片蒐集與使用說明',
  notice: '本說明為草稿，尚未經醫院審核，不具法律效力。',
  sections: [
    ['照片用途', '照片只用於放射治療期間的皮膚照護與追蹤，協助負責照護您的護理人員了解皮膚變化。系統不會用照片自動判斷嚴重程度或分級。'],
    ['誰可以查看', '您本人，以及目前負責照護您的醫療人員。照護關係結束後，對方就不能再查看。系統管理者在此版本不能查看照片，每次查看照片都會留下紀錄。'],
    ['不是即時監測', '照片送出後不會立即有人查看，護理師會在服務時間內查看。上傳照片不代表醫護人員已經看過或已完成評估。'],
    ['緊急狀況', '如有呼吸困難、意識不清等緊急狀況，請立即撥打 119 或就近急診就醫；想詢問皮膚狀況，請於服務時間撥打 07-6150011 分機 2205。'],
    ['拍攝提醒', '請只拍需要照護的皮膚部位，避免拍到臉部、刺青、證件或其他能辨識您身分的內容。系統會移除照片中的拍攝地點、裝置等資訊，但照片畫面本身仍可能辨識出您。'],
    ['撤回與保存', '您可以隨時撤回同意，撤回後不能再上傳照片。送出前移除的照片會被刪除；已送出的照片屬於照護紀錄，保存與刪除方式依醫院規定辦理（待醫院確認）。'],
  ].map(([heading, text]) => ({ heading, text })),
}
// PENDING CLINICAL REVIEW (= backend BODY_SITES)
export const PHOTO_BODY_SITES = [
  ['head_neck', '頭頸部'], ['chest_breast_left', '左側胸部／乳房'], ['chest_breast_right', '右側胸部／乳房'], ['axilla_left', '左腋下'],
  ['axilla_right', '右腋下'], ['abdomen', '腹部'], ['pelvis_perineum', '骨盆／會陰'], ['back', '背部'], ['limb', '四肢'], ['other', '其他部位'],
].map(([code, label]) => ({ code, label }))
const SITE_LABEL = Object.fromEntries(PHOTO_BODY_SITES.map((s) => [s.code, s.label]))
const MAX_PER_RECORD = 3

function load() {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (raw) return JSON.parse(raw)
  } catch { /* storage unavailable */ }
  return { photos: [], consents: {}, keys: {}, images: {} }
}
const state = load()
function save() {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(state))
  } catch {
    try { // images too large for sessionStorage: keep the metadata at least
      sessionStorage.setItem(KEY, JSON.stringify({ ...state, images: {} }))
    } catch { /* storage unavailable */ }
  }
}

const err = (status, code, message, details = []) => new MockApiError(status, code, message, details)
const notFound = () => err(404, 'NOT_FOUND', 'Photo not found')
const taipeiDay = (iso) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei' }).format(new Date(iso))

function consentPayload(patientId) {
  const c = state.consents[patientId] ?? null
  return {
    ...CONSENT, review_status: 'draft', content_sha256: 'mock',
    accepted: !!c && c.version === PHOTO_CONSENT_VERSION, accepted_version: c?.version ?? null, accepted_at: c?.accepted_at ?? null,
  }
}

export function photoPayload(p) {
  return {
    id: p.id, status: p.status, body_site: p.body_site, body_site_label: SITE_LABEL[p.body_site] ?? null, created_at: p.created_at,
    attached_at: p.attached_at, width: p.width, height: p.height, content_type: 'image/jpeg', file_size: p.file_size,
  }
}

/** GET /symptom-photos/settings */
export function mockPhotoSettings() {
  const user = mockCurrentUser('patient', 'nurse')
  const data = {
    upload_enabled: true, view_enabled: true, reason: null, max_per_record: MAX_PER_RECORD, max_bytes: 10 * 1024 * 1024,
    accepted_types: ['image/jpeg', 'image/png', 'image/webp'], heic_supported: false, body_sites: PHOTO_BODY_SITES, body_sites_pending_review: true,
  }
  if (user.role === 'patient') data.consent = consentPayload(mockAccessPatient('me', 'patient').id)
  return { data }
}

export function mockAcceptPhotoConsent(version) {
  const pid = mockAccessPatient('me', 'patient').id
  if (version !== PHOTO_CONSENT_VERSION) throw err(409, 'CONSENT_VERSION_MISMATCH', '同意內容已更新，請重新閱讀後再同意')
  if (state.consents[pid]?.version !== version) state.consents[pid] = { version, accepted_at: nowIso() }
  save()
  return { data: consentPayload(pid) }
}

export function mockRevokePhotoConsent() {
  const pid = mockAccessPatient('me', 'patient').id
  delete state.consents[pid]
  save()
  return { data: consentPayload(pid) }
}

function ensureConsent(pid) {
  if (state.consents[pid]?.version !== PHOTO_CONSENT_VERSION) throw err(403, 'PHOTO_CONSENT_REQUIRED', '請先閱讀並同意照片使用說明')
}

/** POST /patients/{id}/symptom-photos — `upload` = { key, sha, dataUrl, thumbUrl, width, height, size } (prepared in the browser). */
export function mockUploadPhoto(patientId, upload) {
  const user = mockCurrentUser('patient')
  const pid = mockAccessPatient(patientId, 'patient').id
  ensureConsent(pid)
  if (!upload.key) throw err(428, 'IDEMPOTENCY_KEY_REQUIRED', 'Idempotency-Key header is required')
  if (!upload.dataUrl?.startsWith('data:image/jpeg')) throw err(415, 'PHOTO_UNSUPPORTED_TYPE', '只支援 JPEG、PNG、WebP 照片')
  const replay = state.keys[`${user.id}:${upload.key}`]
  if (replay) {
    if (replay.sha !== upload.sha) throw err(422, 'IDEMPOTENCY_KEY_MISMATCH', 'Idempotency-Key was already used with a different request')
    return { data: photoPayload(state.photos.find((p) => p.id === replay.id)) }
  }
  let photo = state.photos.find((p) => p.uploaded_by === user.id && p.patient_id === pid && p.status === 'pending' && p.sha === upload.sha)
  if (!photo) {
    photo = { id: crypto.randomUUID(), patient_id: pid, uploaded_by: user.id, status: 'pending', body_site: null, record_id: null,
      created_at: nowIso(), attached_at: null, width: upload.width, height: upload.height, file_size: upload.size, sha: upload.sha }
    state.photos.push(photo)
    state.images[photo.id] = { full: upload.dataUrl, thumb: upload.thumbUrl }
  }
  state.keys[`${user.id}:${upload.key}`] = { id: photo.id, sha: upload.sha }
  save()
  return { data: photoPayload(photo) }
}

/** DELETE /symptom-photos/{id}: own photo not sent yet; sent photos are kept (409). */
export function mockDiscardPhoto(id) {
  const user = mockCurrentUser('patient')
  const photo = state.photos.find((p) => p.id === id)
  if (!photo || photo.uploaded_by !== user.id || photo.status === 'discarded') throw notFound()
  if (photo.status !== 'pending') throw err(409, 'PHOTO_RETAINED', '已送出的照片屬於照護紀錄，依規定保留；如需處理請聯絡醫療團隊')
  photo.status = 'discarded'
  delete state.images[id]
  save()
  return { data: photoPayload(photo) }
}

/** GET /symptom-photos/{id}/content → { data_url } (the API answers image/jpeg bytes). */
export function mockPhotoContent(id, variant = 'thumb') {
  const user = mockCurrentUser('patient', 'nurse')
  const photo = state.photos.find((p) => p.id === id)
  if (!photo || photo.status === 'discarded') throw notFound()
  if (photo.status === 'pending') {
    if (photo.uploaded_by !== user.id) throw notFound()
  } else {
    mockAccessPatient(photo.patient_id, 'patient', 'nurse')
  }
  const image = state.images[id]
  if (!image) throw notFound()
  return { data: { data_url: variant === 'full' ? image.full : image.thumb } }
}

/** Validation of a report's `photos` before the record is created (= parse_photo_specs + attach_photos checks). */
export function checkMockPhotos(patientId, specs) {
  if (specs == null) return []
  const user = mockCurrentUser('patient')
  if (!Array.isArray(specs)) throw err(400, 'VALIDATION_ERROR', '症狀回報內容有誤', [{ field: 'photos', issue: 'must be a list' }])
  if (specs.length > MAX_PER_RECORD) throw err(400, 'VALIDATION_ERROR', '症狀回報內容有誤', [{ field: 'photos', issue: `at most ${MAX_PER_RECORD} photos per report` }])
  if (new Set(specs.map((s) => s.id)).size !== specs.length) throw err(400, 'VALIDATION_ERROR', '症狀回報內容有誤', [{ field: 'photos', issue: 'appears more than once' }])
  if (specs.length) ensureConsent(patientId)
  return specs.map((s) => {
    if (s.body_site != null && !SITE_LABEL[s.body_site]) throw err(400, 'VALIDATION_ERROR', '症狀回報內容有誤', [{ field: 'photos', issue: 'is not a valid body site' }])
    const photo = state.photos.find((p) => p.id === s.id)
    if (!photo || photo.patient_id !== patientId || photo.uploaded_by !== user.id) throw err(422, 'PHOTO_NOT_FOUND', '找不到這張照片，請重新上傳')
    if (photo.status !== 'pending') throw err(409, 'PHOTO_NOT_AVAILABLE', '這張照片已送出或已移除，請重新選擇')
    return { photo, site: s.body_site ?? null }
  })
}

/** Attach checked photos to a new report → their payloads (stored on the mock record). */
export function attachMockPhotos(recordId, checked) {
  const now = nowIso()
  for (const { photo, site } of checked) Object.assign(photo, { status: 'attached', record_id: recordId, attached_at: now, body_site: site })
  save()
  return checked.map(({ photo }) => photoPayload(photo))
}

/** GET /patients/{id}/symptom-photos (filters: bodySite, from, to — local dates). */
export function mockPatientPhotos(patientId, { bodySite = null, from = null, to = null } = {}) {
  const pid = mockAccessPatient(patientId, 'patient', 'nurse').id
  const records = mockListRecordsStore(pid)
  if (bodySite && !SITE_LABEL[bodySite]) throw err(400, 'VALIDATION_ERROR', '查詢條件有誤', [{ field: 'body_site', issue: 'is not a valid body site' }])
  const byId = Object.fromEntries(records.map((r) => [r.id, r]))
  const visible = state.photos.filter((p) => p.patient_id === pid && p.status === 'attached' && byId[p.record_id]
    && byId[p.record_id].record_status !== 'entered_in_error')
  const items = visible
    .filter((p) => (!bodySite || p.body_site === bodySite) && (!from || taipeiDay(p.created_at) >= from) && (!to || taipeiDay(p.created_at) <= to))
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
    .map((p) => {
      const r = byId[p.record_id]
      const answers = r.values.map((v) => (v.option_label !== undefined ? `${v.label}：${v.option_label}` : `${v.label} ${v.score ?? v.value_numeric}`)).join('、')
      return { ...photoPayload(p), local_date: taipeiDay(p.created_at),
        record: { id: r.id, recorded_at: r.recorded_at, answers, notes: r.notes ?? null, review_status: r.review_status } }
    })
  const used = new Set(visible.map((p) => p.body_site).filter(Boolean))
  return { data: items, meta: { total: items.length, body_sites: PHOTO_BODY_SITES.filter((s) => used.has(s.code)), view_enabled: true } }
}
