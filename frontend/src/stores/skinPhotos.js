import { defineStore } from 'pinia'

import { api, call } from '@/api/call'
import { USE_MOCK } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { blobToDataUrl } from '@/utils/imagePrep'

/**
 * 皮膚照片 (Phase 1b): settings + consent, uploads (pending until the report is sent), images and history.
 * Images are fetched with the signed-in user's token (GET /symptom-photos/{id}/content — no public URL) and
 * shown through object URLs kept here; they are revoked when the page releases them and whenever the
 * signed-in user changes, so a cached image never reaches another account in the same tab.
 */
export const useSkinPhotoStore = defineStore('skinPhotos', {
  state: () => ({
    owner: null,
    settings: null,
    settingsError: null,
    urls: {}, // `${id}:${variant}` → object URL (API) or data URL (mock)
    history: {}, // patientId → { items, meta }
    historyError: {},
  }),

  actions: {
    _own() {
      const uid = useAuthStore().user?.id ?? null
      if (uid !== this.owner) {
        this.releaseImages()
        this.settings = null
        this.history = {}
        this.owner = uid
      }
    },

    async fetchSettings() {
      this._own()
      this.settingsError = null
      try {
        this.settings = (await call('mockPhotoSettings', [], () => api.get('/symptom-photos/settings'))).data
      } catch (err) {
        this.settingsError = err
      }
      return this.settings
    },

    async acceptConsent() {
      const version = this.settings.consent.version
      const body = await call('mockAcceptPhotoConsent', [version], () => api.post('/patients/me/photo-consent', { version }))
      this.settings = { ...this.settings, consent: body.data }
    },

    async revokeConsent() {
      const body = await call('mockRevokePhotoConsent', [], () => api.post('/patients/me/photo-consent/revoke'))
      this.settings = { ...this.settings, consent: body.data }
    },

    /** One prepared photo (utils/imagePrep.js) → the pending photo. Keep `key` for a retry of the same photo. */
    async upload(patientId, prepared, key) {
      this._own()
      if (USE_MOCK) {
        const [dataUrl, thumbUrl] = await Promise.all([blobToDataUrl(prepared.blob), blobToDataUrl(prepared.thumbBlob)])
        const args = [patientId, { key, sha: prepared.sha, dataUrl, thumbUrl, width: prepared.width, height: prepared.height, size: prepared.blob.size }]
        return (await call('mockUploadPhoto', args, null)).data
      }
      const form = new FormData()
      form.append('file', prepared.blob, 'photo.jpg') // never the original file name
      return (await call(null, [], () => api.post(`/patients/${patientId}/symptom-photos`, form, { headers: { 'Idempotency-Key': key } }))).data
    },

    /** Remove a photo not sent yet (best effort: an unsent photo is also cleaned up by the server later). */
    async discard(id) {
      await call('mockDiscardPhoto', [id], () => api.delete(`/symptom-photos/${id}`))
      for (const variant of ['thumb', 'full']) this._release(`${id}:${variant}`)
    },

    /** URL to show one image; fetched once per page with the user's token. */
    async imageUrl(id, variant = 'thumb') {
      this._own()
      const key = `${id}:${variant}`
      if (this.urls[key]) return this.urls[key]
      let url
      if (USE_MOCK) {
        url = (await call('mockPhotoContent', [id, variant], null)).data.data_url
      } else {
        const res = await call(null, [], () => api.get(`/symptom-photos/${id}/content`, { params: { variant }, responseType: 'blob' }))
        url = URL.createObjectURL(res)
      }
      this.urls[key] = url
      return url
    },

    _release(key) {
      const url = this.urls[key]
      if (url?.startsWith('blob:')) URL.revokeObjectURL(url)
      delete this.urls[key]
    },

    releaseImages() {
      for (const key of Object.keys(this.urls)) this._release(key)
    },

    async fetchHistory(patientId, { bodySite = null, from = null, to = null } = {}) {
      this._own()
      this.historyError[patientId] = null
      try {
        const params = { ...(bodySite ? { body_site: bodySite } : {}), ...(from ? { from } : {}), ...(to ? { to } : {}) }
        const body = await call('mockPatientPhotos', [patientId, { bodySite, from, to }], () => api.get(`/patients/${patientId}/symptom-photos`, { params }))
        this.history[patientId] = { items: body.data, meta: body.meta }
      } catch (err) {
        this.historyError[patientId] = err
      }
    },
  },
})
