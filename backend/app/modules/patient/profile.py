"""Patient basic data maintained by the patient (GET / PATCH /patients/me/profile), weight history
and the contact-email verification.

- Email: ``patient_contacts.email`` — the patient's notification email, separate from the login
  account (``users.email``, never changed here). Changing it clears the verification, switches
  email notifications off and invalidates any verification link already sent.
- Email notifications can only be switched on for a verified address; emails go out only when
  the address is verified **and** notifications are on.
- Height: ``patient_profiles.height_cm`` (the same column nurses edit).
- Weight: ``vital_signs.weight_kg`` — recorded through the existing ``POST /vital-signs`` (source
  ``patient_app``, recorded_by = the patient's account); history is kept (append-only).
- BMI: latest final weight / height² — computed, never stored.

Staff (assigned nurse, admin) can read the profile, with the email masked; only the patient
writes it. Callers write audit entries and commit.
"""

import hashlib
import secrets
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from flask import current_app
from sqlalchemy import select, update

from app.core.api import APIError
from app.core.timeutil import iso_utc
from app.extensions import db
from app.models import AuthToken, PatientContact, VitalSign
from app.models.base import utcnow
from app.models.enums import RecordStatus, RoleName, TokenType
from app.modules.patient import management as m
from app.services.email import get_email_service, send_email
from app.services.email.templates import mask_email, verification_email

PATIENT_FIELDS = ("email", "height_cm", "email_notification_enabled")
MAX_WEIGHTS = 100


def _error(details):
    raise APIError(400, "VALIDATION_ERROR", "基本資料有誤", details)


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def contact_of(patient, create=False):
    contact = patient.contact
    if contact is None and create:
        contact = PatientContact(patient_id=patient.id, email_notification_enabled=False)
        db.session.add(contact)
        patient.contact = contact
    return contact


# ------------------------------------------------------------------ weight / BMI


def _weights_query(patient):
    return (
        select(VitalSign)
        .where(VitalSign.patient_id == patient.id, VitalSign.weight_kg.is_not(None),
               VitalSign.record_status == RecordStatus.FINAL)
        .order_by(VitalSign.measured_at.desc(), VitalSign.id.desc())
    )


def latest_weight(patient):
    return db.session.execute(_weights_query(patient).limit(1)).scalars().first()


def bmi(height_cm, weight_kg):
    """kg / m², one decimal (round half up — the frontend mock rounds the same way); None when a value is missing."""
    if not height_cm or not weight_kg:
        return None
    meters = float(height_cm) / 100
    value = Decimal(repr(float(weight_kg) / (meters * meters)))
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def weight_payload(v, viewer):
    data = {
        "id": v.id,
        "weight_kg": float(v.weight_kg),
        "measured_at": iso_utc(v.measured_at),
        "source": v.source,  # patient_app = entered by the patient; nurse = measured by staff
        "entered_by_patient": v.source == "patient_app",
    }
    if viewer.role_name != RoleName.PATIENT:
        data["recorded_by"] = {"id": v.recorder.public_id, "display_name": v.recorder.display_name} if v.recorder else None
    return data


def weight_history(patient, viewer, limit):
    rows = db.session.execute(_weights_query(patient).limit(limit)).scalars().all()
    return [weight_payload(v, viewer) for v in rows]


# ------------------------------------------------------------------ profile


def _pending_verification(patient):
    if patient.user_id is None:
        return None
    now = utcnow()
    return db.session.execute(
        select(AuthToken).where(
            AuthToken.user_id == patient.user_id, AuthToken.token_type == TokenType.EMAIL_VERIFICATION,
            AuthToken.revoked_at.is_(None), AuthToken.used_at.is_(None), AuthToken.expires_at > now,
        ).order_by(AuthToken.created_at.desc(), AuthToken.id.desc())
    ).scalars().first()


def profile_payload(patient, viewer):
    """Same shape for every viewer; staff get ``email: null`` and only the masked address."""
    contact = contact_of(patient)
    weight = latest_weight(patient)
    own = viewer.role_name == RoleName.PATIENT
    email = contact.email if contact else None
    pending = _pending_verification(patient) if own and email and not (contact and contact.email_verified) else None
    return {
        "patient_id": patient.public_id,
        "patient_code": patient.patient_code,
        "display_name": patient.display_name,
        "height_cm": float(patient.height_cm) if patient.height_cm is not None else None,
        "latest_weight": weight_payload(weight, viewer) if weight else None,
        "bmi": bmi(patient.height_cm, weight.weight_kg if weight else None),
        "email": email if own else None,
        "email_masked": mask_email(email),
        "email_verified": bool(contact and contact.email_verified),
        "email_verified_at": iso_utc(contact.email_verified_at) if contact and contact.email_verified else None,
        "email_notification_enabled": bool(contact and contact.email_notification_enabled),
        "email_verification_sent_at": iso_utc(pending.created_at) if pending else None,
        # false while no real email provider is configured: verification / email notifications cannot be sent
        "email_delivery_available": get_email_service().available,
    }


def contact_summary(patient):
    """For staff views of the patient (GET /patients/{pid}): never the full address."""
    contact = contact_of(patient)
    return {
        "email_masked": mask_email(contact.email if contact else None),
        "email_verified": bool(contact and contact.email_verified),
        "email_notification_enabled": bool(contact and contact.email_notification_enabled),
    }


def _revoke_verifications(patient, now):
    if patient.user_id is None:
        return
    db.session.execute(
        update(AuthToken)
        .where(AuthToken.user_id == patient.user_id, AuthToken.token_type == TokenType.EMAIL_VERIFICATION,
               AuthToken.revoked_at.is_(None), AuthToken.used_at.is_(None))
        .values(revoked_at=now)
    )


def update_profile(patient, body):
    """``{email?, height_cm?, email_notification_enabled?}``. Returns ``{table: [changed fields]}``."""
    details = []
    for key in sorted(set(body) - set(PATIENT_FIELDS)):
        details.append({"field": key, "issue": "cannot be changed here (ask the nursing team)"})

    values = {}
    if "email" in body:
        raw = body["email"]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            values["email"] = None
        elif not isinstance(raw, str) or len(raw.strip()) > 255 or not m.EMAIL_PATTERN.match(raw.strip()):
            details.append({"field": "email", "issue": "must be a valid email address"})
        else:
            values["email"] = raw.strip().lower()
    if "height_cm" in body:
        try:
            values["height_cm"] = m._profile_values({"height_cm": body["height_cm"]}, partial=True)["height_cm"]
        except APIError as err:
            details += err.details
    if "email_notification_enabled" in body:
        if not isinstance(body["email_notification_enabled"], bool):
            details.append({"field": "email_notification_enabled", "issue": "must be true or false"})
        else:
            values["email_notification_enabled"] = body["email_notification_enabled"]
    if details:
        _error(details)

    changed = {"patient_profiles": [], "patient_contacts": []}
    now = utcnow()
    contact = contact_of(patient, create=bool({"email", "email_notification_enabled"} & set(values)))

    if "height_cm" in values:
        new = values["height_cm"]
        old = float(patient.height_cm) if patient.height_cm is not None else None
        if old != new:
            patient.height_cm = new
            changed["patient_profiles"].append("height_cm")

    if "email" in values and values["email"] != contact.email:
        contact.email = values["email"]
        contact.email_verified_at = None  # a new address must be verified again
        if contact.email_notification_enabled:
            contact.email_notification_enabled = False
            changed["patient_contacts"].append("email_notification_enabled")
        _revoke_verifications(patient, now)  # links sent for the old address stop working
        changed["patient_contacts"].insert(0, "email")

    if "email_notification_enabled" in values:
        enable = values["email_notification_enabled"]
        if enable and not contact.email_verified:
            raise APIError(422, "EMAIL_NOT_VERIFIED", "請先完成 Email 驗證，才能開啟 Email 通知",
                           [{"field": "email_notification_enabled", "issue": "the email address must be verified first"}])
        if bool(contact.email_notification_enabled) != enable:
            contact.email_notification_enabled = enable
            if "email_notification_enabled" not in changed["patient_contacts"]:
                changed["patient_contacts"].append("email_notification_enabled")
    return {k: v for k, v in changed.items() if v}


# ------------------------------------------------------------------ email verification


def request_verification(patient):
    """Create a one-time link (24 h) for the current contact email and send it. Earlier links stop
    working. Returns the SendResult status / error code. Caller audits and commits **before** the
    email is sent (``send_verification``)."""
    contact = contact_of(patient)
    if contact is None or not contact.email:
        raise APIError(422, "NO_EMAIL", "請先填寫 Email", [{"field": "email", "issue": "is required"}])
    if contact.email_verified:
        raise APIError(409, "CONFLICT", "這個 Email 已經驗證過了")
    if not get_email_service().available:
        raise APIError(422, "EMAIL_NOT_CONFIGURED", "系統目前尚未開放 Email 寄送，暫時無法驗證 Email")
    now = utcnow()
    latest = _pending_verification(patient)
    wait = current_app.config["EMAIL_VERIFICATION_RESEND_SECONDS"]
    if latest is not None and latest.created_at > now - timedelta(seconds=wait):
        retry = int((latest.created_at + timedelta(seconds=wait) - now).total_seconds()) + 1
        raise APIError(429, "RATE_LIMITED", f"驗證信剛寄出，請 {retry} 秒後再試", [{"field": "retry_after", "issue": str(retry)}])
    _revoke_verifications(patient, now)
    raw = secrets.token_urlsafe(32)
    db.session.add(AuthToken(
        user_id=patient.user_id, token_type=TokenType.EMAIL_VERIFICATION, token_hash=_hash(raw),
        family_id=str(uuid.uuid4()), expires_at=now + timedelta(hours=current_app.config["EMAIL_VERIFICATION_HOURS"]),
    ))
    db.session.flush()
    return raw, contact.email, now


def send_verification(patient, raw, address, now):
    """After commit: send the link. The link and token never reach logs or the audit trail."""
    link = f"{current_app.config['APP_BASE_URL']}/patient/verify-email#token={raw}"
    message = verification_email(address, link, current_app.config["EMAIL_VERIFICATION_HOURS"], patient.timezone, now)
    return send_email(message)


def confirm_verification(patient, raw):
    """Mark the contact email verified with a link sent to this patient's account. Email
    notifications stay as they are (off) — the patient switches them on afterwards."""
    if not isinstance(raw, str) or not raw or len(raw) > 200:
        raise APIError(400, "VALIDATION_ERROR", "驗證連結有誤", [{"field": "token", "issue": "is required"}])
    token = db.session.execute(
        select(AuthToken).where(AuthToken.token_hash == _hash(raw), AuthToken.token_type == TokenType.EMAIL_VERIFICATION)
    ).scalar_one_or_none()
    contact = contact_of(patient)
    if token is None or token.user_id != patient.user_id or contact is None or not contact.email:
        raise APIError(422, "VERIFICATION_LINK_INVALID", "驗證連結無效，請重新寄送驗證信")
    if token.used_at is not None:
        raise APIError(422, "VERIFICATION_LINK_USED", "這個驗證連結已經使用過了")
    if token.revoked_at is not None:
        raise APIError(422, "VERIFICATION_LINK_INVALID", "驗證連結已失效（Email 已變更或已重新寄送），請使用最新的驗證信")
    now = utcnow()
    if token.expires_at <= now:
        raise APIError(422, "VERIFICATION_LINK_EXPIRED", "驗證連結已過期，請重新寄送驗證信")
    token.used_at = now
    contact.email_verified_at = now
