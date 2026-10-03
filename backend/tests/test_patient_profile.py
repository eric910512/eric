"""Patient profile + email notification sprint: basic data the patient maintains (GET / PATCH
/patients/me/profile), weight history on the existing vital_signs (patient-entered, read by the
existing Risk Engine unchanged), BMI (computed), contact-email verification (one-time link, 24 h,
invalidated by a new address), permissions (patient: self only; nurse / admin: masked read, no
writes) and audit (field names only — no address, link or token)."""
import sys
from pathlib import Path
import json
import uuid
import warnings
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from app import create_app
from app.extensions import db
from app.models import AuditLog, AuthToken, NursePatientAssignment, PatientContact, PatientProfile, Role, User, VitalSign
from app.models.base import utcnow
from app.models.enums import ObservationSource, RecordStatus, TokenType
from app.seeds.dev import seed_dev_data
from werkzeug.security import generate_password_hash
from app.services.email.disabled import DisabledEmailService

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()
outbox = app.extensions["email_service"].outbox

PROFILE_KEYS = {
    "patient_id", "patient_code", "display_name", "height_cm", "latest_weight", "bmi", "email", "email_masked",
    "email_verified", "email_verified_at", "email_notification_enabled", "email_verification_sent_at",
    "email_delivery_available",
}
WEIGHT_KEYS = {"id", "weight_kg", "measured_at", "source", "entered_by_patient"}


def token(email, password="Demo@1234"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password}).get_json()["data"]["access_token"]


def H(t, key=None):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": key or str(uuid.uuid4())}


def link_token(message):
    return message["text"].split("#token=", 1)[1].split()[0]


def age_tokens(user_id, seconds):
    """Pretend the verification links of this account were sent ``seconds`` earlier (resend wait)."""
    for t in db.session.query(AuthToken).filter_by(user_id=user_id, token_type=TokenType.EMAIL_VERIFICATION):
        t.created_at -= timedelta(seconds=seconds)
    db.session.commit()


with app.app_context():
    db.create_all()
    seed_dev_data()
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    # a second patient with an account, cared for by a second nurse only (nurse01: P00001 only)
    nurse2 = User(role=roles["nurse"], email="nurse02@demo.local", password_hash=pw, display_name="測試護理師 陳", password_changed_at=utcnow())
    u2 = User(role=roles["patient"], email="p2@demo.local", password_hash=pw, display_name="乙", password_changed_at=utcnow())
    p2 = PatientProfile(patient_code="P09002", display_name="測試病人 乙", date_of_birth=date(1960, 1, 1), user=u2)
    db.session.add_all([nurse2, p2])
    db.session.flush()
    db.session.add(NursePatientAssignment(nurse_id=nurse2.id, patient_id=p2.id))
    db.session.commit()
    pt, p2t, nt, at = token("patient01@demo.local"), token("p2@demo.local"), token("nurse01@demo.local"), token("admin01@demo.local")
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    p3 = p2  # not assigned to nurse01
    pid, pid2 = p1.public_id, p2.public_id
    login_email = p1.user.email
    me = "/api/v1/patients/me/profile"

    # ================================================================ read own profile
    r = c.get(me, headers=H(pt))
    d = r.get_json()["data"]
    check("patient GET /patients/me/profile → 200 with the documented shape", r.status_code == 200 and set(d) == PROFILE_KEYS, sorted(d))
    check("no contact email yet: not verified, notifications off", d["email"] is None and d["email_verified"] is False
          and d["email_notification_enabled"] is False and d["email_masked"] is None)
    check("capture provider in tests → email delivery available", d["email_delivery_available"] is True)
    check("own uuid works like me", c.get(f"/api/v1/patients/{pid}/profile", headers=H(pt)).get_json()["data"]["patient_id"] == pid)

    # ================================================================ update email / height
    r = c.patch(me, json={"email": "  Patient.One@Example.TEST ", "height_cm": 170}, headers=H(pt))
    d = r.get_json()["data"]
    check("patient updates own email + height → 200", r.status_code == 200, r.get_json())
    check("email trimmed and lower-cased; unverified; notifications stay off",
          d["email"] == "patient.one@example.test" and d["email_verified"] is False and d["email_notification_enabled"] is False, d)
    check("login account email (users.email) unchanged", db.session.get(User, p1.user_id).email == login_email)
    check("contact email stored in patient_contacts", db.session.query(PatientContact).filter_by(patient_id=p1.id).one().email == "patient.one@example.test")
    check("height saved on patient_profiles", float(db.session.get(PatientProfile, p1.id).height_cm) == 170.0)

    r = c.patch(me, json={"height_cm": 20}, headers=H(pt))
    check("height out of range → 400", r.status_code == 400 and r.get_json()["error"]["details"][0]["field"] == "height_cm", r.get_json())
    r = c.patch(me, json={"email": "not-an-email"}, headers=H(pt))
    check("invalid email → 400", r.status_code == 400 and r.get_json()["error"]["details"][0]["field"] == "email")
    r = c.patch(me, json={"display_name": "改名", "allergies": "x", "national_id": "A123"}, headers=H(pt))
    fields = sorted(x["field"] for x in r.get_json()["error"]["details"])
    check("fields the patient may not change → 400 (nurse-maintained data untouched)", r.status_code == 400 and fields == ["allergies", "display_name", "national_id"], fields)
    check("…and nothing was changed", db.session.get(PatientProfile, p1.id).display_name != "改名")
    r = c.patch(me, json={"email_notification_enabled": "yes"}, headers=H(pt))
    check("email_notification_enabled must be boolean → 400", r.status_code == 400)

    # ================================================================ permissions
    r = c.patch(f"/api/v1/patients/{pid2}/profile", json={"email": "evil@example.test"}, headers=H(pt))
    check("patient cannot update another patient's email → 404", r.status_code == 404, r.status_code)
    check("…other patient's contact untouched", db.session.query(PatientContact).filter_by(patient_id=p2.id).first() is None)
    check("patient cannot read another patient's profile → 404", c.get(f"/api/v1/patients/{pid2}/profile", headers=H(pt)).status_code == 404)
    check("patient cannot read another patient's weights → 404", c.get(f"/api/v1/patients/{pid2}/weights", headers=H(pt)).status_code == 404)
    check("nurse cannot PATCH a patient's profile (assigned or not) → 403",
          c.patch(f"/api/v1/patients/{pid}/profile", json={"email": "n@example.test"}, headers=H(nt)).status_code == 403)
    check("admin cannot PATCH a patient's profile → 403",
          c.patch(f"/api/v1/patients/{pid}/profile", json={"email": "a@example.test"}, headers=H(at)).status_code == 403)
    r = c.patch(f"/api/v1/patients/{pid}", json={"email": "n@example.test"}, headers=H(nt))
    check("nurse PATCH /patients/{pid} with email → 400 (not a profile field)",
          r.status_code == 400 and r.get_json()["error"]["details"][0]["field"] == "email", r.get_json())
    check("…contact email unchanged", db.session.query(PatientContact).filter_by(patient_id=p1.id).one().email == "patient.one@example.test")
    check("nurse cannot request a verification email for a patient → 403",
          c.post(f"/api/v1/patients/{pid}/email-verification", headers=H(nt)).status_code == 403)

    # staff read: masked only
    r = c.get(f"/api/v1/patients/{pid}/profile", headers=H(nt))
    d = r.get_json()["data"]
    check("assigned nurse reads the profile: same shape, email null, masked address", r.status_code == 200 and set(d) == PROFILE_KEYS
          and d["email"] is None and d["email_masked"] == "p***@example.test", d)
    check("assigned nurse does not get the full address anywhere in the response", "patient.one@example.test" not in r.get_data(as_text=True))
    d = c.get(f"/api/v1/patients/{pid}", headers=H(nt)).get_json()["data"]
    check("staff patient detail: notification_contact masked + verified + enabled",
          d["notification_contact"] == {"email_masked": "p***@example.test", "email_verified": False, "email_notification_enabled": False}, d.get("notification_contact"))
    check("staff patient detail: no full contact email", "patient.one@example.test" not in json.dumps(d))
    check("patient's own GET /patients/me has no staff-only notification_contact", "notification_contact" not in c.get("/api/v1/patients/me", headers=H(pt)).get_json()["data"])
    r = c.get(f"/api/v1/patients/{pid}/profile", headers=H(at))
    check("admin reads the profile masked", r.status_code == 200 and r.get_json()["data"]["email"] is None and r.get_json()["data"]["email_masked"] == "p***@example.test")
    check("nurse reading a non-assigned patient's profile → 404", c.get(f"/api/v1/patients/{p3.public_id}/profile", headers=H(nt)).status_code == 404)

    # ================================================================ notifications need a verified email
    r = c.patch(me, json={"email_notification_enabled": True}, headers=H(pt))
    check("enable email notifications before verification → 422 EMAIL_NOT_VERIFIED",
          r.status_code == 422 and r.get_json()["error"]["code"] == "EMAIL_NOT_VERIFIED", r.get_json())
    check("…stays false in the database", db.session.query(PatientContact).filter_by(patient_id=p1.id).one().email_notification_enabled is False)

    # ================================================================ verification
    before = len(outbox)
    r = c.post("/api/v1/patients/me/email-verification", headers=H(pt))
    body = r.get_json()["data"]
    check("request verification → 200, delivery sent", r.status_code == 200 and body["delivery"] == {"status": "sent", "error_code": None}, body)
    check("profile shows the pending verification", body["profile"]["email_verification_sent_at"] is not None)
    msg = outbox[-1]
    check("one verification email to the contact address (not the login email)", len(outbox) == before + 1 and msg["to"] == "patient.one@example.test" and msg["purpose"] == "verification")
    check("link → web app /patient/verify-email#token=…", "/patient/verify-email#token=" in msg["text"])
    check("verification email holds no patient code / ids / login email", all(x not in msg["text"] for x in ("P00001", pid, login_email, p1.user.public_id)))
    raw1 = link_token(msg)
    stored = db.session.query(AuthToken).filter_by(user_id=p1.user_id, token_type=TokenType.EMAIL_VERIFICATION).all()
    check("token stored hashed only (auth_tokens, type email_verification)", len(stored) == 1 and stored[0].token_hash != raw1 and raw1 not in stored[0].token_hash)
    check("valid for 24 hours", abs((stored[0].expires_at - stored[0].created_at) - timedelta(hours=24)) < timedelta(seconds=5))
    check("not a session: the refresh-session list ignores it",
          all(s["id"] != stored[0].family_id for s in c.get("/api/v1/auth/sessions", headers=H(pt)).get_json()["data"]))

    r = c.post("/api/v1/patients/me/email-verification", headers=H(pt))
    check("second request within 60 s → 429 RATE_LIMITED", r.status_code == 429 and r.get_json()["error"]["code"] == "RATE_LIMITED", r.status_code)

    # a newer link replaces the older one
    age_tokens(p1.user_id, 120)
    c.post("/api/v1/patients/me/email-verification", headers=H(pt))
    raw2 = link_token(outbox[-1])
    r = c.post("/api/v1/patients/me/email-verification/confirm", json={"token": raw1}, headers=H(pt))
    check("older link after a resend → 422 VERIFICATION_LINK_INVALID", r.status_code == 422 and r.get_json()["error"]["code"] == "VERIFICATION_LINK_INVALID", r.get_json())

    # another patient cannot use the link
    r = c.post("/api/v1/patients/me/email-verification/confirm", json={"token": raw2}, headers=H(p2t))
    check("another patient using the link → 422 (bound to the account it was sent to)", r.status_code == 422)
    check("nurse cannot confirm → 403", c.post(f"/api/v1/patients/{pid}/email-verification/confirm", json={"token": raw2}, headers=H(nt)).status_code == 403)
    check("missing token → 400", c.post("/api/v1/patients/me/email-verification/confirm", json={}, headers=H(pt)).status_code == 400)

    # expired
    t2 = db.session.query(AuthToken).filter_by(user_id=p1.user_id, token_type=TokenType.EMAIL_VERIFICATION, revoked_at=None).one()
    real_expiry = t2.expires_at
    t2.expires_at = utcnow() - timedelta(minutes=1)
    db.session.commit()
    r = c.post("/api/v1/patients/me/email-verification/confirm", json={"token": raw2}, headers=H(pt))
    check("expired link → 422 VERIFICATION_LINK_EXPIRED", r.status_code == 422 and r.get_json()["error"]["code"] == "VERIFICATION_LINK_EXPIRED", r.get_json())
    t2.expires_at = real_expiry
    db.session.commit()

    r = c.post("/api/v1/patients/me/email-verification/confirm", json={"token": raw2}, headers=H(pt))
    d = r.get_json()["data"]
    check("valid link → verified", r.status_code == 200 and d["email_verified"] is True and d["email_verified_at"] is not None, r.get_json())
    check("verification alone does not switch notifications on", d["email_notification_enabled"] is False)
    r = c.post("/api/v1/patients/me/email-verification/confirm", json={"token": raw2}, headers=H(pt))
    check("same link again → 422 VERIFICATION_LINK_USED (one use)", r.status_code == 422 and r.get_json()["error"]["code"] == "VERIFICATION_LINK_USED")
    check("verified address: request another link → 409", c.post("/api/v1/patients/me/email-verification", headers=H(pt)).status_code == 409)

    r = c.patch(me, json={"email_notification_enabled": True}, headers=H(pt))
    check("after verification: email notifications can be switched on", r.status_code == 200 and r.get_json()["data"]["email_notification_enabled"] is True)
    r = c.patch(me, json={"email_notification_enabled": False}, headers=H(pt))
    check("…and off again", r.status_code == 200 and r.get_json()["data"]["email_notification_enabled"] is False)
    c.patch(me, json={"email_notification_enabled": True}, headers=H(pt))

    # changing the email resets everything and kills pending links
    age_tokens(p1.user_id, 120)
    c.patch(me, json={"email": "second@example.test"}, headers=H(pt))
    c.post("/api/v1/patients/me/email-verification", headers=H(pt))
    raw3 = link_token(outbox[-1])
    check("verification email for the new address goes to the new address", outbox[-1]["to"] == "second@example.test")
    r = c.patch(me, json={"email": "third@example.test"}, headers=H(pt))
    d = r.get_json()["data"]
    check("new email → unverified and notifications off", d["email_verified"] is False and d["email_notification_enabled"] is False, d)
    r = c.post("/api/v1/patients/me/email-verification/confirm", json={"token": raw3}, headers=H(pt))
    check("link sent for the previous address no longer works → 422", r.status_code == 422 and r.get_json()["error"]["code"] == "VERIFICATION_LINK_INVALID")
    check("unverified address: notifications cannot be switched on", c.patch(me, json={"email_notification_enabled": True}, headers=H(pt)).status_code == 422)
    r = c.patch(me, json={"email": None}, headers=H(pt))
    check("email can be removed (null)", r.status_code == 200 and r.get_json()["data"]["email"] is None and r.get_json()["data"]["email_verified"] is False)
    r = c.post("/api/v1/patients/me/email-verification", headers=H(pt))
    check("no email → verification request 422 NO_EMAIL", r.status_code == 422 and r.get_json()["error"]["code"] == "NO_EMAIL")

    # ================================================================ weight history + BMI
    c.patch(me, json={"height_cm": 170}, headers=H(pt))
    vurl = "/api/v1/vital-signs"
    before_rows = db.session.query(VitalSign).filter(VitalSign.patient_id == p1.id, VitalSign.weight_kg.is_not(None)).count()
    r1 = c.post(vurl, json={"patient_id": "me", "weight_kg": 66.0}, headers=H(pt))
    r2 = c.post(vurl, json={"patient_id": "me", "weight_kg": 65.0}, headers=H(pt))
    check("patient adds weights through the existing POST /vital-signs → 201", r1.status_code == 201 and r2.status_code == 201, (r1.status_code, r2.status_code))
    rows = db.session.query(VitalSign).filter(VitalSign.patient_id == p1.id, VitalSign.weight_kg.is_not(None)).all()
    check("a new weight adds a row: history kept (no update)", len(rows) == before_rows + 2)
    newest = db.session.get(VitalSign, r2.get_json()["data"]["id"])
    check("patient-entered weight: source patient_app, recorded_by = the patient's own account",
          newest.source == ObservationSource.PATIENT_APP and newest.recorded_by == p1.user_id)
    audit = db.session.query(AuditLog).filter_by(resource_type="vital_signs", resource_id=str(newest.id), action="CREATE").one()
    check("audit shows who entered it (actor = patient account, role patient)", audit.actor_user_id == p1.user_id and audit.actor_role == "patient")

    d = c.get(me, headers=H(pt)).get_json()["data"]
    check("latest weight = newest entry; marked as entered by the patient", d["latest_weight"]["weight_kg"] == 65.0
          and d["latest_weight"]["source"] == "patient_app" and d["latest_weight"]["entered_by_patient"] is True
          and set(d["latest_weight"]) == WEIGHT_KEYS, d["latest_weight"])
    check("BMI = 65 / 1.70² = 22.5", d["bmi"] == 22.5, d["bmi"])
    w = c.get(f"/api/v1/patients/{pid}/weights", headers=H(pt)).get_json()
    check("weight history newest first, older entries kept", [x["weight_kg"] for x in w["data"][:2]] == [65.0, 66.0] and w["meta"]["total"] >= 3, w["data"][:3])
    check("patient's history has no staff names", all("recorded_by" not in x for x in w["data"]))
    ws = c.get(f"/api/v1/patients/{pid}/weights", headers=H(nt)).get_json()["data"]
    check("nurse sees who recorded each weight", ws[0]["recorded_by"]["id"] == p1.user.public_id)

    # nurse-measured weight is labelled as such; a corrected weight leaves the latest
    rn = c.post(vurl, json={"patient_id": pid, "weight_kg": 64.0}, headers=H(nt))
    d = c.get(me, headers=H(pt)).get_json()["data"]
    check("nurse-measured weight: source nurse, not marked as patient-entered",
          d["latest_weight"]["weight_kg"] == 64.0 and d["latest_weight"]["source"] == "nurse" and d["latest_weight"]["entered_by_patient"] is False)
    r = c.post(f"{vurl}/{rn.get_json()['data']['id']}/mark-error", json={"reason": "量錯人"}, headers=H(nt))
    d = c.get(me, headers=H(pt)).get_json()["data"]
    check("weight marked entered-in-error leaves the latest weight / BMI", r.status_code == 200 and d["latest_weight"]["weight_kg"] == 65.0 and d["bmi"] == 22.5, (r.status_code, d["latest_weight"]))
    check("BMI is not stored (no bmi column)", not hasattr(PatientProfile, "bmi") and not hasattr(VitalSign, "bmi"))
    c.patch(me, json={"height_cm": None}, headers=H(pt))
    check("no height → BMI null", c.get(me, headers=H(pt)).get_json()["data"]["bmi"] is None)
    c.patch(me, json={"height_cm": 170}, headers=H(pt))

    # ================================================================ the existing Risk Engine reads patient-entered weights
    # baseline 7.5 days earlier (an earlier patient entry), then the patient enters a lower weight now
    db.session.add(VitalSign(patient_id=p1.id, measured_at=utcnow() - timedelta(days=7, hours=12), weight_kg=Decimal("70.0"),
                             recorded_by=p1.user_id, source=ObservationSource.PATIENT_APP, record_status=RecordStatus.FINAL))
    db.session.commit()
    r = c.post(vurl, json={"patient_id": "me", "weight_kg": 66.5}, headers=H(pt))
    check("patient enters 66.5 kg (−5% in 7 days)", r.status_code == 201)
    widgets = c.get(f"/api/v1/dashboard/patient/{pid}", headers=H(nt)).get_json()["data"]["widgets"]
    lw = widgets["latest-vitals"]["weight_kg"]
    check("existing latest-vitals reads the patient-entered weight and its 7-day change",
          lw["value"] == 66.5 and lw["change_pct_7d"] == -5.0 and lw["flag"] == "warning", lw)
    risk = widgets["nurse-view"]["risk"]
    check("existing Risk Engine rule (weight_change_pct_7d ≤ −3%) raises medium with the unchanged reason text",
          risk["level"] in ("medium", "high") and any(x.startswith("7 天體重變化 -5") for x in risk["reasons"]), risk)
    check("threshold unchanged (config)", app.config["VITAL_REFERENCE_RANGES"]["weight_change_pct_7d"] == {"warning_low": -3.0})

    # ================================================================ provider not configured
    real = app.extensions["email_service"]
    app.extensions["email_service"] = DisabledEmailService(app.config)
    age_tokens(p1.user_id, 120)
    c.patch(me, json={"email": "fourth@example.test"}, headers=H(pt))
    d = c.get(me, headers=H(pt)).get_json()["data"]
    check("no provider: email_delivery_available false", d["email_delivery_available"] is False)
    n_out = len(outbox)
    r = c.post("/api/v1/patients/me/email-verification", headers=H(pt))
    check("no provider: verification request → 422 EMAIL_NOT_CONFIGURED, nothing sent",
          r.status_code == 422 and r.get_json()["error"]["code"] == "EMAIL_NOT_CONFIGURED" and len(outbox) == n_out, r.get_json())
    app.extensions["email_service"] = real

    # ================================================================ audit
    rows = db.session.query(AuditLog).filter(AuditLog.patient_id == p1.id, AuditLog.resource_type.in_(("patient_contacts", "patient_profiles"))).all()
    blob = json.dumps([{"changes": a.changes, "reason": a.reason, "endpoint": a.endpoint} for a in rows], ensure_ascii=False)
    check("profile updates audited (UPDATE patient_contacts / patient_profiles)", any(a.resource_type == "patient_contacts" for a in rows)
          and any(a.resource_type == "patient_profiles" and (a.changes or {}).get("fields") == ["height_cm"] for a in rows))
    check("email change audited by field name with verification reset", any((a.changes or {}).get("email_verification_reset") for a in rows))
    check("verification request / confirm audited", any((a.changes or {}).get("email_verification_requested") for a in rows)
          and any((a.changes or {}).get("email_verified") for a in rows))
    all_audit = json.dumps([[a.changes, a.reason, a.endpoint, a.actor_identifier] for a in db.session.query(AuditLog).all()], ensure_ascii=False)
    secrets_seen = [s for s in ("example.test", raw1, raw2, raw3, "verify-email", "#token=", "Demo@1234") if s in all_audit]
    check("audit never holds an email address, verification link, token or password", not secrets_seen, secrets_seen)
    check("patient profile audit is not the login identifier", login_email not in blob)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
