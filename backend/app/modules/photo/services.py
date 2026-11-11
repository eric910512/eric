"""皮膚照片 (Phase 1b): consent, upload (pending), attach to a symptom record, list, read, discard, clean-up.

Flow (chosen to avoid orphan / duplicate photos and partial failures):

1. The patient uploads each photo on its own (Idempotency-Key per photo) → re-encoded, stored privately,
   ``pending`` (visible to the uploader only). A retry of the same upload returns the same photo; the
   same picture uploaded again while still pending returns that photo too (no duplicate).
2. The symptom report (POST /symptoms/records) lists the photo ids: the record and the attachment are
   one transaction — either the report exists with all its photos, or nothing changed and the photos
   stay pending (the form keeps every answer; the patient can retry).
3. Pending photos never attached are discarded after PHOTO_PENDING_TTL_HOURS (image removed from
   storage, metadata kept): opportunistically at each upload and with ``flask photos purge-pending``.
   An upload whose database commit fails removes its stored files again (compensation).

Attached photos are part of the care record: nothing here deletes them (retention policy pending the
hospital). No photo is ever analysed or graded automatically.
"""

from datetime import timedelta

from flask import current_app
from sqlalchemy import func, select, update

from app.core.api import APIError
from app.core.audit import record_data_event
from app.core.timeutil import iso_utc, local_day_bounds_utc, patient_zone, to_local
from app.extensions import db
from app.models import AuditLog, PatientConsent, SymptomPhoto
from app.models.base import generate_uuid, utcnow
from app.models.enums import (AuditAction, AuditCategory, AuditOutcome, PhotoDeleteReason, PhotoStatus, RecordStatus,
                              RoleName, SymptomValueType)
from app.modules.photo import consent as consent_text
from app.services.image_processing import ALLOWED_TYPES, STORED_TYPE, ImageRejected, process
from app.services.photo_storage import PhotoStorageError, photo_feature

PHOTO_FORMS = ("rt_skin_self_assessment",)  # forms whose reports may carry photos
TZ = "Asia/Taipei"
# PENDING CLINICAL REVIEW: affected-area list (photos only; never changes a symptom answer)
BODY_SITES = [
    ("head_neck", "頭頸部"),
    ("chest_breast_left", "左側胸部／乳房"),
    ("chest_breast_right", "右側胸部／乳房"),
    ("axilla_left", "左腋下"),
    ("axilla_right", "右腋下"),
    ("abdomen", "腹部"),
    ("pelvis_perineum", "骨盆／會陰"),
    ("back", "背部"),
    ("limb", "四肢"),
    ("other", "其他部位"),
]
BODY_SITE_LABEL = dict(BODY_SITES)


def _cfg(name):
    return current_app.config[name]


def feature():
    return photo_feature(current_app)


def settings_payload(patient=None):
    f = feature()
    data = {
        "upload_enabled": f.upload_enabled,
        "view_enabled": f.view_enabled,
        "reason": f.reason,
        "max_per_record": _cfg("PHOTO_MAX_PER_RECORD"),
        "max_bytes": _cfg("PHOTO_MAX_UPLOAD_BYTES"),
        "accepted_types": list(ALLOWED_TYPES),
        "heic_supported": False,
        "body_sites": [{"code": c, "label": label} for c, label in BODY_SITES],
        "body_sites_pending_review": True,
    }
    if patient is not None:
        data["consent"] = consent_payload(patient)
    return data


# ------------------------------------------------------------------ consent


def active_consent(patient):
    return db.session.execute(select(PatientConsent).filter_by(
        patient_id=patient.id, consent_type=consent_text.CONSENT_TYPE, revoked_at=None)).scalar_one_or_none()


def consent_payload(patient):
    current = active_consent(patient)
    return {
        **consent_text.content(),
        "review_status": consent_text.REVIEW_STATUS,  # draft = 待院方審核
        "content_sha256": consent_text.content_sha256(),
        "accepted": current is not None and current.version == consent_text.CONSENT_VERSION,
        "accepted_version": current.version if current else None,
        "accepted_at": iso_utc(current.accepted_at) if current else None,
    }


def accept_consent(patient, user, version):
    if version != consent_text.CONSENT_VERSION:
        raise APIError(409, "CONSENT_VERSION_MISMATCH", "同意內容已更新，請重新閱讀後再同意", [
            {"field": "version", "issue": f"current version is {consent_text.CONSENT_VERSION}"}])
    current = active_consent(patient)
    if current is not None and current.version == version:
        return current, False
    now = utcnow()
    if current is not None:  # an older version: replaced by the new acceptance (audited as its revocation)
        current.revoked_at, current.revoked_by = now, user.id
        db.session.flush()
        record_data_event(AuditAction.UPDATE, "patient_consents", current.id, patient=patient,
                          changes={"revoked": True, "version": current.version, "superseded_by": version})
    row = PatientConsent(patient_id=patient.id, consent_type=consent_text.CONSENT_TYPE, version=version,
                         content_sha256=consent_text.content_sha256(), accepted_by=user.id, accepted_at=now)
    db.session.add(row)
    db.session.flush()
    record_data_event(AuditAction.CREATE, "patient_consents", row.id, patient=patient,
                      changes={"consent_type": row.consent_type, "version": version, "content_sha256": row.content_sha256})
    return row, True


def revoke_consent(patient, user):
    current = active_consent(patient)
    if current is None:
        return None
    current.revoked_at, current.revoked_by = utcnow(), user.id
    record_data_event(AuditAction.UPDATE, "patient_consents", current.id, patient=patient,
                      changes={"revoked": True, "version": current.version})
    return current


def ensure_upload_allowed(patient):
    f = feature()
    if not f.upload_enabled:
        raise APIError(503, "PHOTO_UPLOADS_DISABLED", "照片上傳功能目前未開放")
    current = active_consent(patient)
    if current is None or current.version != consent_text.CONSENT_VERSION:
        raise APIError(403, "PHOTO_CONSENT_REQUIRED", "請先閱讀並同意照片使用說明")


# ------------------------------------------------------------------ payloads


def photo_payload(photo):
    return {
        "id": photo.public_id,
        "status": photo.status,
        "body_site": photo.body_site,
        "body_site_label": BODY_SITE_LABEL.get(photo.body_site),
        "created_at": iso_utc(photo.created_at),  # upload time (the original capture time is not kept)
        "attached_at": iso_utc(photo.attached_at),
        "width": photo.width,
        "height": photo.height,
        "content_type": photo.content_type,
        "file_size": photo.file_size,
    }


def _visible_record(record):
    """Photos of a record marked entered-in-error (e.g. the wrong patient) are not shown."""
    return record is not None and record.record_status != RecordStatus.ENTERED_IN_ERROR


def record_photos(record):
    """Attached photos of a record and of the versions it corrects (photos stay on the original)."""
    ids, cur = [], record
    while cur is not None:
        ids.append(cur.id)
        cur = cur.amends
    if record.record_status == RecordStatus.ENTERED_IN_ERROR:
        return []
    rows = db.session.execute(select(SymptomPhoto).where(
        SymptomPhoto.symptom_record_id.in_(ids), SymptomPhoto.status == PhotoStatus.ATTACHED,
    ).order_by(SymptomPhoto.created_at, SymptomPhoto.id)).scalars().all()
    return [photo_payload(p) for p in rows]


def _answers_text(record):
    parts = []
    for v in record.ordered_values():
        d = v.definition
        if d.value_type == SymptomValueType.SINGLE_CHOICE and v.option:
            parts.append(f"{d.name_zh}：{v.option.label_zh}")
        elif d.value_type == SymptomValueType.BOOLEAN:
            parts.append(f"{d.name_zh}：{'有' if v.value_boolean else '沒有'}")
        elif v.score is not None:
            parts.append(f"{d.name_zh} {float(v.score):g}")
    return "、".join(parts)


# ------------------------------------------------------------------ upload


def _check_rate(user, patient):
    now = utcnow()
    recent = db.session.execute(select(func.count()).select_from(SymptomPhoto).where(
        SymptomPhoto.uploaded_by == user.id, SymptomPhoto.created_at >= now - timedelta(minutes=10))).scalar()
    start, end = local_day_bounds_utc(to_local(now, patient_zone(patient.timezone or TZ)).date(), patient_zone(patient.timezone or TZ))
    today = db.session.execute(select(func.count()).select_from(SymptomPhoto).where(
        SymptomPhoto.uploaded_by == user.id, SymptomPhoto.created_at >= start, SymptomPhoto.created_at < end)).scalar()
    if recent >= _cfg("PHOTO_MAX_PER_10_MINUTES") or today >= _cfg("PHOTO_MAX_PER_DAY"):
        raise APIError(429, "RATE_LIMITED", "上傳照片的次數太多，請稍後再試")


def prepare_upload(data, declared_type):
    """Validate + re-encode (no database / storage work). Raises APIError for a rejected image."""
    try:
        return process(data, declared_type, max_bytes=_cfg("PHOTO_MAX_UPLOAD_BYTES"), max_pixels=_cfg("PHOTO_MAX_PIXELS"),
                       min_side=_cfg("PHOTO_MIN_SIDE"), full_side=_cfg("PHOTO_FULL_MAX_SIDE"), thumb_side=_cfg("PHOTO_THUMB_MAX_SIDE"))
    except ImageRejected as exc:
        raise APIError(exc.status, exc.code, exc.message) from None


def create_pending_photo(patient, user, image, stored_keys):
    """Store the re-encoded image and add a pending row (caller commits). ``stored_keys`` collects the keys
    written, so the caller can remove them if the commit fails. Returns (photo, created)."""
    duplicate = db.session.execute(select(SymptomPhoto).where(
        SymptomPhoto.uploaded_by == user.id, SymptomPhoto.patient_id == patient.id,
        SymptomPhoto.status == PhotoStatus.PENDING, SymptomPhoto.source_sha256 == image.source_sha256)).scalar_one_or_none()
    if duplicate is not None:
        return duplicate, False  # the same picture is already waiting to be sent: no second copy
    _check_rate(user, patient)
    storage = feature().storage
    photo = SymptomPhoto(patient_id=patient.id, uploaded_by=user.id, status=PhotoStatus.PENDING, storage_backend=storage.name,
                         content_type=STORED_TYPE, file_size=len(image.full), width=image.width, height=image.height,
                         thumb_file_size=len(image.thumb), checksum_sha256=image.checksum_sha256, source_sha256=image.source_sha256)
    photo.public_id = generate_uuid()
    folder = generate_uuid().replace("-", "")  # unrelated to the public id: the key reveals nothing
    photo.storage_key, photo.thumb_storage_key = f"photos/{folder}/full.jpg", f"photos/{folder}/thumb.jpg"
    try:
        storage.put(photo.storage_key, image.full, STORED_TYPE)
        stored_keys.append(photo.storage_key)
        storage.put(photo.thumb_storage_key, image.thumb, STORED_TYPE)
        stored_keys.append(photo.thumb_storage_key)
    except PhotoStorageError:
        raise APIError(503, "PHOTO_STORAGE_UNAVAILABLE", "照片暫時無法儲存，請稍後再試") from None
    db.session.add(photo)
    db.session.flush()
    record_data_event(AuditAction.CREATE, "symptom_photos", photo.public_id, patient=patient,
                      changes={"status": PhotoStatus.PENDING, "file_size": photo.file_size, "width": photo.width, "height": photo.height})
    return photo, True


def remove_stored(keys):
    """Remove stored files, best effort (a leftover is found and removed by ``flask photos verify --fix``)."""
    storage = feature().storage
    for key in keys:
        try:
            storage.delete(key)
        except (PhotoStorageError, ValueError):
            pass


def photo_keys(photo):
    return [photo.storage_key, photo.thumb_storage_key]


def _mark_discarded(photo, reason, actor_id):
    """pending → discarded, **only if it is still pending** (conditional update): a report attaching the same
    photo at the same moment either wins (the photo stays attached, nothing is deleted) or loses. The files
    are removed by the caller only after this state change is committed — never before."""
    now = utcnow()
    result = db.session.execute(
        update(SymptomPhoto).where(SymptomPhoto.id == photo.id, SymptomPhoto.status == PhotoStatus.PENDING)
        .values(status=PhotoStatus.DISCARDED, deleted_at=now, deleted_by=actor_id, delete_reason=reason, updated_at=now)
        .execution_options(synchronize_session=False))
    db.session.refresh(photo)
    return result.rowcount == 1


def discard_pending(photo, user):
    """Patient removes an unsent photo (caller commits, then deletes ``photo_keys(photo)``)."""
    if photo.status != PhotoStatus.PENDING or not _mark_discarded(photo, PhotoDeleteReason.REMOVED_BEFORE_SUBMIT, user.id):
        raise APIError(409, "PHOTO_RETAINED", "已送出的照片屬於照護紀錄，依規定保留；如需處理請聯絡醫療團隊")
    record_data_event(AuditAction.DELETE, "symptom_photos", photo.public_id, patient=photo.patient,
                      changes={"reason": PhotoDeleteReason.REMOVED_BEFORE_SUBMIT})


def purge_expired_pending(limit=50, now=None):
    """Discard pending photos older than the TTL. Commits, then removes their files. Works without a
    request (CLI). Safe to repeat: attached photos are never touched; a file whose removal failed is
    left for ``flask photos verify --fix``."""
    now = now or utcnow()
    cutoff = now - timedelta(hours=_cfg("PHOTO_PENDING_TTL_HOURS"))
    rows = db.session.execute(select(SymptomPhoto).where(
        SymptomPhoto.status == PhotoStatus.PENDING, SymptomPhoto.created_at < cutoff,
    ).order_by(SymptomPhoto.created_at).limit(limit)).scalars().all()
    discarded = []
    for photo in rows:
        if not _mark_discarded(photo, PhotoDeleteReason.EXPIRED_UNATTACHED, None):
            continue  # attached (or removed) in the meantime: leave it alone
        discarded.append(photo)
        db.session.add(AuditLog(  # system action: no user, no request
            actor_identifier="system:photo-cleanup", category=AuditCategory.DATA, action=AuditAction.DELETE,
            resource_type="symptom_photos", resource_id=photo.public_id, patient_id=photo.patient_id,
            changes={"reason": PhotoDeleteReason.EXPIRED_UNATTACHED}, outcome=AuditOutcome.SUCCESS,
        ))
    if discarded:
        db.session.commit()
        for photo in discarded:
            remove_stored(photo_keys(photo))
    return len(discarded)


def verify_storage(fix=False):
    """Consistency between photo rows and stored files (``flask photos verify``):

    - ``missing``: a pending / attached photo whose file is gone — reported, **never fixed automatically**
      (a care record is affected; someone has to look).
    - ``leftovers``: files of discarded photos still stored (e.g. a removal that failed) — removed with ``fix``.
    - ``orphans`` (local storage only, it can be listed): files without any row — removed with ``fix``.
    """
    from app.services.photo_storage import LocalPrivateStorage

    storage = feature().storage
    rows = db.session.execute(select(SymptomPhoto).order_by(SymptomPhoto.id)).scalars().all()
    missing, leftovers = [], []
    for p in rows:
        for variant, key in (("full", p.storage_key), ("thumb", p.thumb_storage_key)):
            present = storage.exists(key)
            if p.status in (PhotoStatus.PENDING, PhotoStatus.ATTACHED) and not present:
                missing.append({"photo": p.public_id, "status": p.status, "variant": variant})
            elif p.status == PhotoStatus.DISCARDED and present:
                leftovers.append(key)
    orphans = []
    if isinstance(storage, LocalPrivateStorage):
        known = {k for p in rows for k in photo_keys(p)}
        orphans = [k for k in storage.keys() if k not in known]
    if fix:
        remove_stored(leftovers + orphans)
    return {"checked": len(rows), "missing": missing, "leftovers": len(leftovers), "orphans": len(orphans), "fixed": fix}


# ------------------------------------------------------------------ attach (inside create_symptom_record)


def parse_photo_specs(body, form, errors):
    """``photos: [{id, body_site?}]`` of a symptom report → list of (public_id, body_site). Validation only."""
    raw = body.get("photos")
    if raw is None:
        return []
    if form.code not in PHOTO_FORMS:
        errors.add("photos", "this form does not take photos")
        return []
    if not isinstance(raw, list):
        errors.add("photos", "must be a list")
        return []
    if len(raw) > _cfg("PHOTO_MAX_PER_RECORD"):
        errors.add("photos", f"at most {_cfg('PHOTO_MAX_PER_RECORD')} photos per report")
        return []
    specs, seen = [], set()
    for idx, item in enumerate(raw):
        pid = item.get("id") if isinstance(item, dict) else None
        site = item.get("body_site") if isinstance(item, dict) else None
        if not isinstance(pid, str) or not pid or len(pid) > 36:
            errors.add(f"photos[{idx}].id", "is required")
        elif pid in seen:
            errors.add(f"photos[{idx}].id", "appears more than once")
        elif site is not None and site not in BODY_SITE_LABEL:
            errors.add(f"photos[{idx}].body_site", "is not a valid body site")
        else:
            seen.add(pid)
            specs.append((pid, site))
    return specs


def attach_photos(record, patient, user, specs):
    """Attach pending photos of this patient, uploaded by this user, to the new record (caller commits;
    any problem raises before the commit → the record is not created and the photos stay pending)."""
    if not specs:
        return []
    ensure_upload_allowed(patient)
    now = utcnow()
    photos = db.session.execute(select(SymptomPhoto).where(SymptomPhoto.public_id.in_([p for p, _ in specs]))).scalars().all()
    by_id = {p.public_id: p for p in photos}
    sources = set()
    for pid, _site in specs:
        photo = by_id.get(pid)
        if photo is None or photo.patient_id != patient.id or photo.uploaded_by != user.id:
            raise APIError(422, "PHOTO_NOT_FOUND", "找不到這張照片，請重新上傳", [{"field": "photos", "issue": f"{pid} not found"}])
        if photo.source_sha256 in sources:
            raise APIError(422, "PHOTO_DUPLICATE", "同一張照片只能附一次", [{"field": "photos", "issue": f"{pid} is a duplicate"}])
        sources.add(photo.source_sha256)
    attached = []
    for pid, site in specs:
        # conditional update: two reports racing for the same pending photo → only one wins
        result = db.session.execute(
            update(SymptomPhoto).where(SymptomPhoto.public_id == pid, SymptomPhoto.status == PhotoStatus.PENDING)
            .values(status=PhotoStatus.ATTACHED, symptom_record_id=record.id, attached_at=now, body_site=site, updated_at=now)
            .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise APIError(409, "PHOTO_NOT_AVAILABLE", "這張照片已送出或已移除，請重新選擇", [{"field": "photos", "issue": f"{pid} is not pending"}])
        attached.append(pid)
    for p in photos:
        db.session.refresh(p)
        record_data_event(AuditAction.UPDATE, "symptom_photos", p.public_id, patient=patient,
                          changes={"status": {"old": PhotoStatus.PENDING, "new": PhotoStatus.ATTACHED}, "symptom_record_id": record.id,
                                   "body_site": p.body_site})
    return attached


# ------------------------------------------------------------------ read


def find_photo(public_id):
    if not isinstance(public_id, str) or len(public_id) != 36:
        return None
    return db.session.execute(select(SymptomPhoto).filter_by(public_id=public_id)).scalar_one_or_none()


def ensure_can_read(photo, user):
    """Pending: the uploader only. Attached: the patient and the nurses currently assigned (backend check
    on every read; an ended assignment → 404 at once). Admin: 403. Anything else: 404."""
    from app.core.auth import ensure_can_view_patient

    if user.role_name == RoleName.ADMIN:
        raise APIError(403, "FORBIDDEN", "管理者不能查看病人照片")
    if photo is None or photo.status == PhotoStatus.DISCARDED or photo.patient.deleted_at is not None:
        raise APIError(404, "NOT_FOUND", "Photo not found")
    if photo.status == PhotoStatus.PENDING:
        if photo.uploaded_by != user.id:
            raise APIError(404, "NOT_FOUND", "Photo not found")
        return
    if not _visible_record(photo.record):
        raise APIError(404, "NOT_FOUND", "Photo not found")
    ensure_can_view_patient(photo.patient)


def read_bytes(photo, variant):
    f = feature()
    if not f.view_enabled:
        raise APIError(503, "PHOTO_STORAGE_UNAVAILABLE", "照片暫時無法讀取")
    if f.storage.name != photo.storage_backend:
        raise APIError(503, "PHOTO_STORAGE_UNAVAILABLE", "照片暫時無法讀取")
    try:
        data = f.storage.get(photo.thumb_storage_key if variant == "thumb" else photo.storage_key)
    except PhotoStorageError:
        raise APIError(503, "PHOTO_STORAGE_UNAVAILABLE", "照片暫時無法讀取") from None
    if data is None:  # the row exists but the file does not: an integrity problem, not "no access"
        current_app.logger.warning("photo file missing (symptom_photos.id=%s); run `flask photos verify`", photo.id)
        raise APIError(404, "PHOTO_FILE_MISSING", "這張照片的檔案暫時無法取得，請通知醫療團隊")
    return data


def list_patient_photos(patient, *, body_site=None, start=None, end=None):
    """Attached photos of visible records, newest first, each with its report's date and answers."""
    from app.models import SymptomRecord

    filters = [SymptomPhoto.patient_id == patient.id, SymptomPhoto.status == PhotoStatus.ATTACHED,
               SymptomRecord.record_status != RecordStatus.ENTERED_IN_ERROR]
    if body_site:
        filters.append(SymptomPhoto.body_site == body_site)
    if start is not None:
        filters.append(SymptomPhoto.created_at >= start)
    if end is not None:
        filters.append(SymptomPhoto.created_at < end)
    rows = db.session.execute(select(SymptomPhoto).join(SymptomRecord, SymptomPhoto.symptom_record_id == SymptomRecord.id)
                              .where(*filters).order_by(SymptomPhoto.created_at.desc(), SymptomPhoto.id.desc())).scalars().all()
    zone = patient_zone(patient.timezone or TZ)
    out = []
    for p in rows:
        record = p.record
        out.append({**photo_payload(p), "local_date": to_local(p.created_at, zone).date().isoformat(),
                    "record": {"id": record.id, "recorded_at": iso_utc(record.recorded_at), "answers": _answers_text(record),
                               "notes": record.notes, "review_status": record.review_status}})
    # the sites this patient has photos of (any date): the filter choices
    used = db.session.execute(select(SymptomPhoto.body_site).join(SymptomRecord, SymptomPhoto.symptom_record_id == SymptomRecord.id).where(
        SymptomPhoto.patient_id == patient.id, SymptomPhoto.status == PhotoStatus.ATTACHED, SymptomPhoto.body_site.is_not(None),
        SymptomRecord.record_status != RecordStatus.ENTERED_IN_ERROR).distinct()).scalars().all()
    order = [s for s, _ in BODY_SITES]
    return out, [{"code": c, "label": BODY_SITE_LABEL[c]} for c in sorted(used, key=lambda c: order.index(c) if c in order else len(order))]
