/**
 * Prepare a chosen photo in the browser before uploading (皮膚照片 Phase 1b): decode it (EXIF orientation
 * applied), scale it down and re-encode it as JPEG. Less data on mobile networks, and the browser's copy
 * already has no EXIF / GPS. The server still validates and re-encodes everything itself.
 * HEIC / HEIF (iPhone) works only where the browser can decode it (Safari); otherwise a clear message.
 */
export const ACCEPT = 'image/jpeg,image/png,image/webp,image/heic,image/heif'
const ALLOWED = ['image/jpeg', 'image/png', 'image/webp']
const MAX_INPUT_BYTES = 30 * 1024 * 1024
const MAX_OUTPUT_BYTES = 10 * 1024 * 1024

export class PrepError extends Error {
  constructor(code, message) {
    super(message)
    this.code = code
  }
}

export const HEIC_MESSAGE = '目前不支援 HEIC／HEIF 格式。請改用 JPEG：iPhone 可到「設定 > 相機 > 格式」選擇「最相容」，或從相簿重新選擇照片。'

function draw(bitmap, maxSide) {
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height))
  const w = Math.max(1, Math.round(bitmap.width * scale))
  const h = Math.max(1, Math.round(bitmap.height * scale))
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  ctx.fillStyle = '#fff' // transparent PNG → white, like the server
  ctx.fillRect(0, 0, w, h)
  ctx.drawImage(bitmap, 0, 0, w, h)
  return canvas
}

const toBlob = (canvas, quality) => new Promise((resolve, reject) =>
  canvas.toBlob((b) => (b ? resolve(b) : reject(new PrepError('DECODE_FAILED', '無法讀取這張照片'))), 'image/jpeg', quality))

async function sha256(blob) {
  const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/** File → { blob (JPEG ≤ 2048 px), thumbBlob (≤ 320 px), width, height, sha } or throws PrepError. */
export async function prepareImage(file, { maxSide = 2048, thumbSide = 320 } = {}) {
  const heif = /hei[cf]/i.test(file.type) || /\.(heic|heif)$/i.test(file.name ?? '')
  if (!heif && !ALLOWED.includes(file.type)) throw new PrepError('UNSUPPORTED', '只支援 JPEG、PNG、WebP 照片')
  if (file.size > MAX_INPUT_BYTES) throw new PrepError('TOO_LARGE', '照片檔案太大，請改用較小的照片')
  let bitmap
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
  } catch {
    throw heif ? new PrepError('HEIC_NOT_SUPPORTED', HEIC_MESSAGE) : new PrepError('DECODE_FAILED', '無法讀取這張照片，檔案可能已損壞')
  }
  try {
    if (Math.min(bitmap.width, bitmap.height) < 64) throw new PrepError('TOO_SMALL', '照片太小，請重新拍攝')
    const full = draw(bitmap, maxSide)
    const blob = await toBlob(full, 0.9)
    if (blob.size > MAX_OUTPUT_BYTES) throw new PrepError('TOO_LARGE', '照片檔案太大，請改用較小的照片')
    const thumbBlob = await toBlob(draw(bitmap, thumbSide), 0.8)
    return { blob, thumbBlob, width: full.width, height: full.height, sha: await sha256(blob) }
  } finally {
    bitmap.close()
  }
}

export const blobToDataUrl = (blob) => new Promise((resolve, reject) => {
  const reader = new FileReader()
  reader.onload = () => resolve(reader.result)
  reader.onerror = () => reject(reader.error)
  reader.readAsDataURL(blob)
})
