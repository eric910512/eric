"""皮膚照片 API (Phase 1b). Every read checks access in the backend and is audited; image bytes are only
served through ``GET /symptom-photos/{id}/content`` (no public or signed URL)."""

from datetime import date

from flask import Response, current_app, g, request
from werkzeug.exceptions import RequestEntityTooLarge

from app.core.api import APIError, ok, query_choice
from app.core.audit import record_patient_view
from app.core.auth import ensure_can_view_patient, require_auth, resolve_patient
from app.core.idempotency import run_idempotent
from app.core.timeutil import local_day_bounds_utc, patient_zone
from app.extensions import db
from app.models.enums import RoleName
from app.modules.photo import photo_bp
from app.modules.photo.services import (
    BODY_SITE_LABEL,
    accept_consent,
    consent_payload,
    create_pending_photo,
    discard_pending,
    ensure_can_read,
    ensure_upload_allowed,
    feature,
    find_photo,
    list_patient_photos,
    photo_keys,
    photo_payload,
    prepare_upload,
    purge_expired_pending,
    read_bytes,
    remove_stored,
    revoke_consent,
    settings_payload,
)

IMAGE_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Content-Disposition": 'inline; filename="photo.jpg"',  # never the original file name
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Referrer-Policy": "no-referrer",
}


def _own_patient():
    patient = resolve_patient("me")
    ensure_can_view_patient(patient)
    return patient


@photo_bp.get("/symptom-photos/settings")
@require_auth(RoleName.PATIENT, RoleName.NURSE)
def settings():
    """Whether uploads / viewing are on, limits, body sites; for a patient also the consent text and state."""
    patient = _own_patient() if g.current_user.role_name == RoleName.PATIENT else None
    return ok(settings_payload(patient))


@photo_bp.post("/patients/me/photo-consent")
@require_auth(RoleName.PATIENT)
def accept():
    """Accept the current consent version: ``{"version": "..."}`` (the version the patient read)."""
    body = request.get_json(silent=True)
    version = body.get("version") if isinstance(body, dict) else None
    if not isinstance(version, str) or not version:
        raise APIError(400, "VALIDATION_ERROR", "請求內容有誤", [{"field": "version", "issue": "is required"}])
    patient = _own_patient()
    _row, created = accept_consent(patient, g.current_user, version)
    db.session.commit()
    return ok(consent_payload(patient), status=201 if created else 200)


@photo_bp.post("/patients/me/photo-consent/revoke")
@require_auth(RoleName.PATIENT)
def revoke():
    patient = _own_patient()
    revoke_consent(patient, g.current_user)
    db.session.commit()
    return ok(consent_payload(patient))


@photo_bp.post("/patients/<patient_id>/symptom-photos")
@require_auth(RoleName.PATIENT)
def upload(patient_id):
    """One photo per request, multipart field ``file``; Idempotency-Key required. → 201 pending photo
    (200 when the same picture is already pending). Attach it with POST /symptoms/records ``photos``."""
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    ensure_upload_allowed(patient)
    max_bytes = current_app.config["PHOTO_MAX_UPLOAD_BYTES"]
    request.max_content_length = max_bytes + 256 * 1024  # this endpoint only (multipart overhead)
    try:
        files = request.files
    except RequestEntityTooLarge:
        raise APIError(413, "PHOTO_TOO_LARGE", f"照片檔案太大（上限 {max_bytes // (1024 * 1024)} MB）") from None
    try:
        upload_file = files.get("file")
        if upload_file is None or len(files) != 1:
            raise APIError(400, "VALIDATION_ERROR", "請求內容有誤", [{"field": "file", "issue": "exactly one file is required"}])
        data = upload_file.stream.read(max_bytes + 1)
        declared = upload_file.mimetype
    finally:
        for part in files.values():
            part.close()  # large uploads are spooled to temporary files
    purge_expired_pending(limit=20)  # opportunistic clean-up (commits its own work)
    image = prepare_upload(data, declared)  # rejects before anything is stored
    stored = []

    def create():
        photo, created = create_pending_photo(patient, g.current_user, image, stored)
        return photo.public_id, {"data": photo_payload(photo)}, 201 if created else 200

    def replay(public_id):
        return {"data": photo_payload(find_photo(public_id))}

    try:
        return run_idempotent(resource_type="symptom_photos", body={"source_sha256": image.source_sha256, "content_type": declared},
                              create=create, replay=replay, required=True)
    except Exception:
        remove_stored(stored)  # compensation: nothing committed → no stored file without its row
        raise


@photo_bp.delete("/symptom-photos/<photo_id>")
@require_auth(RoleName.PATIENT)
def discard(photo_id):
    """Remove a photo that has not been sent yet (pending, own upload). Sent photos are kept (409)."""
    photo = find_photo(photo_id)
    if photo is None or photo.uploaded_by != g.current_user.id or photo.status == "discarded":
        raise APIError(404, "NOT_FOUND", "Photo not found")
    discard_pending(photo, g.current_user)
    db.session.commit()
    remove_stored(photo_keys(photo))  # only after the state change is committed
    return ok(photo_payload(photo))


def _date(name):
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise APIError(400, "VALIDATION_ERROR", "查詢條件有誤", [{"field": name, "issue": "must be YYYY-MM-DD"}]) from None


@photo_bp.get("/patients/<patient_id>/symptom-photos")
@require_auth(RoleName.PATIENT, RoleName.NURSE)
def patient_photos(patient_id):
    """Sent photos of a patient, newest first, with their report (date, answers). Query: body_site,
    from / to (YYYY-MM-DD, the patient's local dates, inclusive). Patient: own; nurse: assigned."""
    patient = resolve_patient(patient_id)
    ensure_can_view_patient(patient)
    site = request.args.get("body_site") or None
    if site is not None and site not in BODY_SITE_LABEL:
        raise APIError(400, "VALIDATION_ERROR", "查詢條件有誤", [{"field": "body_site", "issue": "is not a valid body site"}])
    zone = patient_zone(patient.timezone)
    start_day, end_day = _date("from"), _date("to")
    items, sites = list_patient_photos(patient, body_site=site,
                                       start=local_day_bounds_utc(start_day, zone)[0] if start_day else None,
                                       end=local_day_bounds_utc(end_day, zone)[1] if end_day else None)
    record_patient_view(patient, resource_type="symptom_photos")
    db.session.commit()
    return ok(items, meta={"total": len(items), "body_sites": sites, "view_enabled": feature().view_enabled})


@photo_bp.get("/symptom-photos/<photo_id>/content")
@require_auth(RoleName.PATIENT, RoleName.NURSE)
def content(photo_id):
    """The image (re-encoded JPEG, no metadata). ``variant=thumb|full``. Access is checked on every
    request; every read is audited (photo id and variant only)."""
    variant = query_choice(request.args, "variant", "thumb", ("thumb", "full"))
    photo = find_photo(photo_id)
    ensure_can_read(photo, g.current_user)
    data = read_bytes(photo, variant)
    record_patient_view(photo.patient, resource_type="symptom_photos", resource_id=f"{photo.public_id}:{variant}")
    db.session.commit()
    return Response(data, status=200, mimetype="image/jpeg", headers=IMAGE_HEADERS)
