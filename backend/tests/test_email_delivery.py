"""Patient profile + email notification sprint: nurse-sent notification → app notification + email.

The notification is committed first; the email (summary only) goes out only for a verified
contact email with email notifications on; a failed / timed-out / crashing email never fails the
request, never rolls the notification back and never touches its lifecycle; scheduled reminders
and risk alerts are not emailed; replays do not re-send; delivery outcomes are recorded in
notification_deliveries and audited without addresses, content or provider details."""
import sys
from pathlib import Path
import json
import uuid
import warnings
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import (AuditLog, AuthToken, Notification, NotificationDelivery, NursePatientAssignment, PatientProfile,
                        Role, User)
from app.models.base import utcnow
from app.models.enums import NotificationType, TimelineEventType, TokenType
from app.seeds.dev import seed_dev_data
from app.services.email.disabled import DisabledEmailService
from app.services.email.factory import configuration_problems

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()
service = app.extensions["email_service"]
outbox = service.outbox
DELIVERY_KEYS = {"channel", "status", "skip_reason", "error_code", "recipient_masked", "attempted_at", "completed_at"}


def token(email, password="Demo@1234"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password}).get_json()["data"]["access_token"]


def H(t, key=None):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": key or str(uuid.uuid4())}


def iso(dt):
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def set_contact(t, user_id, address, *, verify=True, enable=True):
    """Set the contact email through the API (and verify / enable it the way the patient would)."""
    for row in db.session.query(AuthToken).filter_by(user_id=user_id, token_type=TokenType.EMAIL_VERIFICATION):
        row.created_at -= timedelta(minutes=5)  # step past the 60 s resend wait
    db.session.commit()
    c.patch("/api/v1/patients/me/profile", json={"email": address}, headers=H(t))
    if verify:
        c.post("/api/v1/patients/me/email-verification", headers=H(t))
        raw = outbox[-1]["text"].split("#token=", 1)[1].split()[0]
        r = c.post("/api/v1/patients/me/email-verification/confirm", json={"token": raw}, headers=H(t))
        assert r.status_code == 200, r.get_json()
    if enable:
        r = c.patch("/api/v1/patients/me/profile", json={"email_notification_enabled": True}, headers=H(t))
        assert r.status_code == 200, r.get_json()


URL = "/api/v1/notifications"
BODY = {"title": "明日治療提醒", "message": "明日上午 09:00 有治療行程，請提前 15 分鐘報到。", "severity": "info"}


def send(t, pid, key=None, **extra):
    return c.post(URL, json={"patient_id": pid, **BODY, **extra}, headers=H(t, key))


def notification_emails():
    return [m for m in outbox if m["purpose"] == "notification"]


with app.app_context():
    db.create_all()
    seed_dev_data()
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    nurse2 = User(role=roles["nurse"], email="nurse02@demo.local", password_hash=pw, display_name="測試護理師 陳", password_changed_at=utcnow())
    u2 = User(role=roles["patient"], email="p2@demo.local", password_hash=pw, display_name="乙", password_changed_at=utcnow())
    p2 = PatientProfile(patient_code="P09002", display_name="測試病人 乙", date_of_birth=date(1960, 1, 1), user=u2)
    db.session.add_all([nurse2, p2])
    db.session.flush()
    db.session.add(NursePatientAssignment(nurse_id=nurse2.id, patient_id=p2.id))  # nurse01 is not assigned to P09002
    db.session.commit()
    pt, p2t, nt, nt2, at = (token(e) for e in ("patient01@demo.local", "p2@demo.local", "nurse01@demo.local", "nurse02@demo.local", "admin01@demo.local"))
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    pid, pid2 = p1.public_id, p2.public_id

    # ================================================================ 1. no email
    r = send(nt, pid)
    d = r.get_json()["data"]
    check("no contact email: notification created (201, status new)", r.status_code == 201 and d["status"] == "new", r.status_code)
    check("…email skipped / no_email; response shape", d["email_delivery"]["status"] == "skipped" and d["email_delivery"]["skip_reason"] == "no_email"
          and set(d["email_delivery"]) == DELIVERY_KEYS, d["email_delivery"])
    check("…nothing sent", not notification_emails())

    # ================================================================ 2. unverified email
    set_contact(pt, p1.user_id, "unverified@example.test", verify=False, enable=False)
    d = send(nt, pid).get_json()["data"]
    check("unverified email → no email (skipped / not_verified)", d["email_delivery"]["status"] == "skipped"
          and d["email_delivery"]["skip_reason"] == "not_verified" and not notification_emails(), d["email_delivery"])
    check("masked recipient on the delivery", d["email_delivery"]["recipient_masked"] == "u***@example.test")

    # ================================================================ 3. verified, notifications off
    set_contact(pt, p1.user_id, "verified@example.test", verify=True, enable=False)
    d = send(nt, pid).get_json()["data"]
    check("verified but email notifications off → no email (skipped / disabled)",
          d["email_delivery"]["skip_reason"] == "disabled" and not notification_emails(), d["email_delivery"])

    # ================================================================ 4. verified + enabled → sent
    c.patch("/api/v1/patients/me/profile", json={"email_notification_enabled": True}, headers=H(pt))
    key = str(uuid.uuid4())
    r = send(nt, pid, key)
    d = r.get_json()["data"]
    sent_id = d["id"]
    emails = notification_emails()
    check("verified + enabled → 201, email sent", r.status_code == 201 and d["email_delivery"]["status"] == "sent"
          and d["email_delivery"]["attempted_at"] and d["email_delivery"]["completed_at"], d["email_delivery"])
    check("exactly one notification email, to the contact address", len(emails) == 1 and emails[0]["to"] == "verified@example.test")
    check("app notification unchanged by the email: status new, lifecycle intact", d["status"] == "new" and d["status_text"] == "待處理")
    text = emails[0]["subject"] + emails[0]["text"]
    check("email = summary: system name, 「您有一則來自護理團隊的新通知」, time, sign-in hint, security note",
          "化療照護" in text and "您有一則來自護理團隊的新通知" in text and "發送時間" in text and "請登入" in text and "安全提醒" in text, text)
    leaked = [x for x in (BODY["title"], BODY["message"], "09:00", p1.patient_code, pid, p1.user.public_id, p1.user.email, "token") if x in text]
    check("email holds no notification content, patient code, ids, login email or token", not leaked, leaked)
    lst = c.get(f"{URL}?per_page=100", headers=H(pt)).get_json()["data"]
    mine = next((n for n in lst if n["id"] == sent_id), None)
    check("patient receives the app notification with the full content", mine is not None and mine["message"] == BODY["message"])
    check("patient payload carries no delivery details", mine is not None and "email_delivery" not in mine)

    # replay
    r2 = send(nt, pid, key)
    check("same Idempotency-Key → same notification, email not sent again", r2.status_code == 201 and r2.get_json()["data"]["id"] == sent_id
          and len(notification_emails()) == 1 and r2.get_json()["data"]["email_delivery"]["status"] == "sent")
    check("one delivery row per notification", db.session.query(NotificationDelivery).filter_by(notification_id=sent_id).count() == 1)

    # staff list / detail
    lst = c.get(f"{URL}?type=reminder&patient_id={pid}&per_page=100", headers=H(nt)).get_json()["data"]
    row = next(n for n in lst if n["id"] == sent_id)
    check("staff reminder list shows email_delivery", row["email_delivery"]["status"] == "sent")
    check("staff detail shows email_delivery", c.get(f"{URL}/{sent_id}", headers=H(nt)).get_json()["data"]["email_delivery"]["status"] == "sent")

    # ================================================================ 5. provider failure does not roll back
    set_contact(pt, p1.user_id, "patient+fail@example.test")
    n_before = db.session.query(Notification).count()
    r = send(nt, pid)
    d = r.get_json()["data"]
    check("email rejected by provider → request still 201", r.status_code == 201, r.status_code)
    check("…delivery failed with our own error code", d["email_delivery"]["status"] == "failed" and d["email_delivery"]["error_code"] == "PROVIDER_REJECTED", d["email_delivery"])
    n = db.session.get(Notification, d["id"])
    check("…notification row committed (not rolled back), status new", n is not None and n.status == "new" and db.session.query(Notification).count() == n_before + 1)
    check("…patient still gets the app notification", any(x["id"] == d["id"] for x in c.get(f"{URL}?per_page=100", headers=H(pt)).get_json()["data"]))
    dl = db.session.query(NotificationDelivery).filter_by(notification_id=d["id"]).one()
    check("…failure recorded with attempted_at / completed_at", dl.status == "failed" and dl.attempted_at and dl.completed_at and dl.error_code == "PROVIDER_REJECTED")
    failed_id = d["id"]

    set_contact(pt, p1.user_id, "patient+timeout@example.test")
    r = send(nt, pid)
    d = r.get_json()["data"]
    check("provider timeout → 201, delivery failed / TIMEOUT, notification kept",
          r.status_code == 201 and d["email_delivery"]["error_code"] == "TIMEOUT" and db.session.get(Notification, d["id"]) is not None, d["email_delivery"])

    set_contact(pt, p1.user_id, "patient@example.test")
    original = service.send

    def boom(message):
        raise RuntimeError("provider said: api_key=sk-secret-value rejected for patient@example.test")

    service.send = boom
    r = send(nt, pid)
    d = r.get_json()["data"]
    service.send = original
    check("provider crash → 201, delivery failed / PROVIDER_ERROR", r.status_code == 201 and d["email_delivery"]["status"] == "failed"
          and d["email_delivery"]["error_code"] == "PROVIDER_ERROR", d["email_delivery"])
    crash_row = db.session.query(NotificationDelivery).filter_by(notification_id=d["id"]).one()
    check("no provider detail stored on the delivery", "sk-secret" not in json.dumps({k: str(getattr(crash_row, k)) for k in DELIVERY_KEYS | {"provider_message_id", "provider"}}))

    # ================================================================ 6. lifecycle untouched by email
    for action in ("acknowledge", "start"):
        r = c.post(f"{URL}/{failed_id}/{action}", headers=H(nt))
    r = c.post(f"{URL}/{failed_id}/resolve", json={"resolution_note": "已電話聯絡"}, headers=H(nt))
    d = r.get_json()["data"]
    check("reminder with a failed email still goes new → acknowledged → in_progress → resolved",
          r.status_code == 200 and d["status"] == "resolved", (r.status_code, d.get("status")))
    check("…and its email delivery stays failed (separate status)", d["email_delivery"]["status"] == "failed")
    check("transitions do not send email", all(m["purpose"] == "notification" for m in notification_emails()) and
          len([m for m in outbox if m["to"] == "patient+fail@example.test" and m["purpose"] == "notification"]) == 1)

    # ================================================================ 7. scheduled reminder: not emailed
    before = len(notification_emails())
    r = send(nt, pid, scheduled_for=iso(utcnow() + timedelta(hours=3)))
    d = r.get_json()["data"]
    check("scheduled reminder → 201, email skipped / scheduled, nothing sent", r.status_code == 201 and d["email_delivery"]["status"] == "skipped"
          and d["email_delivery"]["skip_reason"] == "scheduled" and len(notification_emails()) == before, d["email_delivery"])
    sch = c.get(f"{URL}/scheduled?patient_id={pid}", headers=H(nt)).get_json()["data"]
    check("staff scheduled list shows the same skipped delivery", any(x["id"] == d["id"] and x["email_delivery"]["skip_reason"] == "scheduled" for x in sch))
    check("scheduled reminder still hidden from the patient until due", all(x["id"] != d["id"] for x in c.get(f"{URL}?per_page=100", headers=H(pt)).get_json()["data"]))

    # ================================================================ 8. permissions
    before_rows = db.session.query(NotificationDelivery).count()
    r = send(nt, pid2)
    check("nurse → patient not assigned → 404, no notification, no delivery, no email",
          r.status_code == 404 and db.session.query(NotificationDelivery).count() == before_rows and len(notification_emails()) == before)
    check("patient cannot send notifications → 403", send(pt, pid).status_code == 403)
    r = send(at, pid)
    check("admin may send (existing rule) → 201 with the email channel", r.status_code == 201 and r.get_json()["data"]["email_delivery"]["status"] == "sent")
    set_contact(p2t, u2.id, "second@example.test")
    r = send(nt2, pid2)
    check("nurse assigned to the patient → email to that patient's contact address only",
          r.status_code == 201 and r.get_json()["data"]["email_delivery"]["status"] == "sent" and notification_emails()[-1]["to"] == "second@example.test")

    # ================================================================ 9. risk alerts are not emailed
    before = len(notification_emails())
    r = c.post("/api/v1/vital-signs", json={"patient_id": "me", "temperature_c": 39.2}, headers=H(pt))
    alerts = db.session.query(Notification).filter_by(source_table="vital_signs", source_id=r.get_json()["data"]["id"]).all()
    check("a risk alert is raised by the existing engine", r.status_code == 201 and alerts and all(a.type == NotificationType.RISK_ALERT for a in alerts))
    check("risk alerts get no email and no delivery row", len(notification_emails()) == before
          and db.session.query(NotificationDelivery).filter(NotificationDelivery.notification_id.in_([a.id for a in alerts])).count() == 0)
    staff_alert = c.get(f"{URL}?patient_id={pid}&per_page=5", headers=H(nt)).get_json()["data"][0]
    check("staff risk alert payload: email_delivery null", staff_alert["type"] == "risk_alert" and staff_alert["email_delivery"] is None)

    # ================================================================ 10. provider not configured
    app.extensions["email_service"] = DisabledEmailService(app.config)
    before = len(outbox)
    r = send(nt, pid)
    d = r.get_json()["data"]
    check("no provider configured → 201, delivery skipped / not_configured, nothing attempted",
          r.status_code == 201 and d["email_delivery"]["status"] == "skipped" and d["email_delivery"]["skip_reason"] == "not_configured"
          and d["email_delivery"]["attempted_at"] is None and len(outbox) == before, d["email_delivery"])
    app.extensions["email_service"] = service
    check("capture (mock) provider refused in staging / production",
          configuration_problems({"EMAIL_PROVIDER": "capture"}, production_like=True) != [])
    check("disabled provider accepted in staging / production", configuration_problems({"EMAIL_PROVIDER": "disabled"}, production_like=True) == [])
    check("unknown provider refused everywhere", configuration_problems({"EMAIL_PROVIDER": "smtp"}, production_like=False) != [])

    # ================================================================ 11. timeline unchanged
    events = c.get(f"/api/v1/patients/{pid}/timeline?limit=100", headers=H(nt)).get_json()["data"]
    types = {e["event_type"] for e in events}
    check("timeline: no new event types (email deliveries are not timeline events)", types <= set(TimelineEventType.ALL), types)
    check("timeline: reminders still not shown (risk alerts only, unchanged)",
          all(e.get("source_table") != "notifications" or "提醒" not in json.dumps(e, ensure_ascii=False) for e in events)
          and not any(BODY["title"] in json.dumps(e, ensure_ascii=False) for e in events))

    # ================================================================ 12. audit
    creates = db.session.query(AuditLog).filter_by(resource_type="notifications", action="CREATE").all()
    check("CREATE notifications audit carries the planned email status", all("email_delivery" in a.changes for a in creates) and creates)
    updates = db.session.query(AuditLog).filter_by(resource_type="notification_deliveries").all()
    check("each attempted delivery audited (UPDATE notification_deliveries: status / error code)",
          updates and all(set(a.changes) == {"notification_id", "channel", "status", "error_code"} for a in updates))
    blob = json.dumps([[a.changes, a.reason, a.actor_identifier] for a in db.session.query(AuditLog).all()], ensure_ascii=False)
    found = [x for x in ("example.test", "sk-secret", "#token=", "verify-email", "Demo@1234", "您有一則來自護理團隊的新通知") if x in blob]
    check("audit never holds addresses, email text, provider messages, links, tokens or passwords", not found, found)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
