"""Email content policy for nurse-sent notifications (email_mode none / summary / full, topic
``category``): none sends nothing; summary shows the nurse's title only for non-sensitive topics;
full sends title + content for schedule / preparation; a sensitive topic asking for full is
downgraded to summary by the backend (201, requested / applied / downgraded recorded on the delivery
row and in the audit); omitted fields → clinical_other + summary; invalid values → 400; HTML escaped,
nurse URLs not linked; no ids / tokens / passwords / login email in any email; the app notification,
its lifecycle, risk alerts (never emailed) and verification are unchanged; failures never roll back."""
import sys
from pathlib import Path
import json
import uuid
import warnings
from datetime import date, timedelta, timezone

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
from app.models.enums import TokenType
from app.modules.notification import email_policy
from app.modules.notification.services import TRANSITIONS
from app.seeds.dev import seed_dev_data
from app.services.email.disabled import DisabledEmailService

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()
service = app.extensions["email_service"]
outbox = service.outbox
URL = "/api/v1/notifications"
GENERIC = "您有一則來自護理團隊的醫療照護通知"


def token(email, password="Demo@1234"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password}).get_json()["data"]["access_token"]


def H(t, key=None):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": key or str(uuid.uuid4())}


def verify_contact(t, user_id, address):
    for row in db.session.query(AuthToken).filter_by(user_id=user_id, token_type=TokenType.EMAIL_VERIFICATION):
        row.created_at -= timedelta(minutes=5)
    db.session.commit()
    c.patch("/api/v1/patients/me/profile", json={"email": address}, headers=H(t))
    c.post("/api/v1/patients/me/email-verification", headers=H(t))
    raw = outbox[-1]["text"].split("#token=", 1)[1].split()[0]
    assert c.post("/api/v1/patients/me/email-verification/confirm", json={"token": raw}, headers=H(t)).status_code == 200
    assert c.patch("/api/v1/patients/me/profile", json={"email_notification_enabled": True}, headers=H(t)).status_code == 200


def emails():
    return [m for m in outbox if m["purpose"] == "notification"]


def send(t, pid, title, message, key=None, **extra):
    r = c.post(URL, json={"patient_id": pid, "title": title, "message": message, **extra}, headers=H(t, key))
    return r, (r.get_json() or {}).get("data")


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
    db.session.add(NursePatientAssignment(nurse_id=nurse2.id, patient_id=p2.id))
    db.session.commit()
    pt, nt, at = token("patient01@demo.local"), token("nurse01@demo.local"), token("admin01@demo.local")
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    pid = p1.public_id
    verify_contact(pt, p1.user_id, "patient.one@example.test")
    forbidden_values = [p1.patient_code, pid, p1.user.public_id, p1.user.email, "Demo@1234", "#token=", "Bearer"]

    # ================================================================ policy unit
    check("policy: schedule / preparation allow full", email_policy.resolve("schedule", "full")[2] == "full"
          and email_policy.resolve("preparation", "full")[2] == "full")
    check("policy: medication / symptom_followup / clinical_other → summary",
          all(email_policy.resolve(cat, "full")[2] == "summary" for cat in ("medication", "symptom_followup", "clinical_other")))
    check("policy: no topic → clinical_other; no mode → summary", email_policy.resolve(None, None) == ("clinical_other", "summary", "summary"))
    check("policy: none stays none for every topic", all(email_policy.resolve(cat, "none")[2] == "none" for cat in email_policy.CATEGORIES))

    # ================================================================ 1. none
    before = len(emails())
    r, d = send(nt, pid, "下週回診時間", "下週三 10:00 回診。", category="schedule", email_mode="none")
    e = d["email_delivery"]
    check("none → 201, notification created, no email", r.status_code == 201 and d["status"] == "new" and len(emails()) == before, r.status_code)
    check("none → delivery skipped / not_requested, modes recorded", e["status"] == "skipped" and e["skip_reason"] == "not_requested"
          and e["mode_requested"] == "none" and e["mode"] == "none" and e["category"] == "schedule" and e["downgraded"] is False, e)
    lst = c.get(f"{URL}?per_page=100", headers=H(pt)).get_json()["data"]
    check("none → the patient still gets the full app notification", any(n["id"] == d["id"] and n["message"] == "下週三 10:00 回診。" for n in lst))

    # ================================================================ 2. summary (non-sensitive: title shown)
    r, d = send(nt, pid, "明日治療提醒", "明日上午 09:00 有治療行程，請提前 15 分鐘報到。", category="schedule", email_mode="summary")
    m = emails()[-1]
    check("summary + schedule → sent", r.status_code == 201 and d["email_delivery"]["status"] == "sent" and d["email_delivery"]["mode"] == "summary")
    check("summary subject = 癌症照護系統｜您有一則新通知", m["subject"] == "癌症照護系統｜您有一則新通知", m["subject"])
    check("summary (non-sensitive) shows the nurse's title", "通知標題：\n明日治療提醒" in m["text"], m["text"])
    check("summary never contains the content", "09:00" not in m["text"] and "09:00" not in (m["html"] or ""))
    check("summary: 您好 / 新通知 / 發送時間 / 為保護您的醫療資訊 / 登入癌症照護系統", all(x in m["text"] for x in (
        "您好，", "您有一則來自護理團隊的新通知。", "發送時間：", "為保護您的醫療資訊，完整內容請登入癌症照護系統查看。", "登入癌症照護系統：")))
    check("sign-in link → APP_BASE_URL/patient/notifications (no ids)", f"{app.config['APP_BASE_URL']}/patient/notifications" in m["text"]
          and str(d["id"]) not in m["text"].split("/patient/notifications")[1].split()[0])

    # ================================================================ 2b. summary (sensitive: generic title)
    r, d = send(nt, pid, "WBC 2.1 偏低，化療延後", "白血球偏低，下週再評估。", category="medication", email_mode="summary")
    m = emails()[-1]
    check("summary + medication → generic title, original title hidden", GENERIC in m["text"] and "WBC" not in m["text"]
          and "WBC" not in m["subject"] and "WBC" not in (m["html"] or ""), m["text"])

    # ================================================================ 3/4. full for a non-sensitive topic
    title, message = "就診前準備", "明天早上 6 點後請空腹。\n請攜帶健保卡與藥袋。"
    r, d = send(nt, pid, title, message, category="preparation", email_mode="full")
    m = emails()[-1]
    e = d["email_delivery"]
    check("full + preparation → 201, sent as full", r.status_code == 201 and e["status"] == "sent" and e["mode"] == "full"
          and e["mode_requested"] == "full" and e["downgraded"] is False, e)
    check("full subject = 癌症照護系統｜{title}", m["subject"] == f"癌症照護系統｜{title}", m["subject"])
    check("full body: 標題 / 內容 / 發送時間 / 登入", all(x in m["text"] for x in ("標題：\n就診前準備", "內容：\n明天早上 6 點後請空腹。\n請攜帶健保卡與藥袋。", "發送時間：", "登入癌症照護系統：")), m["text"])
    check("full HTML keeps the line breaks", "明天早上 6 點後請空腹。<br>請攜帶健保卡與藥袋。" in (m["html"] or ""))

    # ================================================================ 5/7. sensitive topic asking for full → downgraded by the backend
    title, message = "檢驗結果說明", "您的 ANC 為 0.8，請避免出入人多的地方。"
    before_n = db.session.query(Notification).count()
    for cat in ("medication", "symptom_followup", "clinical_other"):
        r, d = send(nt, pid, title, message, category=cat, email_mode="full")
        m = emails()[-1]
        e = d["email_delivery"]
        check(f"{cat} + full (direct API) → 201, downgraded to summary", r.status_code == 201 and e["mode_requested"] == "full"
              and e["mode"] == "summary" and e["downgraded"] is True and e["status"] == "sent", e)
        check(f"{cat} + full → email has neither the content nor the title", "ANC" not in m["text"] and "ANC" not in (m["html"] or "")
              and "檢驗結果說明" not in m["text"] and "檢驗結果說明" not in m["subject"] and GENERIC in m["text"])
    row = db.session.query(NotificationDelivery).filter_by(notification_id=d["id"]).one()
    check("downgrade stored on notification_deliveries (requested full, applied summary)",
          row.content_category == "clinical_other" and row.email_mode_requested == "full" and row.email_mode == "summary")
    audit = db.session.query(AuditLog).filter_by(resource_type="notifications", resource_id=str(d["id"]), action="CREATE").one()
    check("downgrade recorded in the audit", audit.changes["email_delivery"]["downgraded"] is True
          and audit.changes["email_delivery"]["mode_requested"] == "full" and audit.changes["email_delivery"]["mode"] == "summary", audit.changes)
    check("the app notification keeps the full content", db.session.get(Notification, d["id"]).message == message)
    r, d = send(nt, pid, title, message, email_mode="full")
    check("no topic + full → clinical_other, downgraded", d["email_delivery"]["category"] == "clinical_other" and d["email_delivery"]["downgraded"] is True)
    r, d = send(at, pid, title, message, category="medication", email_mode="full")
    check("admin cannot bypass the policy either", r.status_code == 201 and d["email_delivery"]["mode"] == "summary")

    # ================================================================ defaults + validation
    r, d = send(nt, pid, "一般提醒", "內容")
    check("omitted category / mode → clinical_other + summary (earlier behaviour)", d["email_delivery"]["category"] == "clinical_other"
          and d["email_delivery"]["mode"] == "summary" and d["email_delivery"]["mode_requested"] == "summary")
    for body, field in (({"category": "lab"}, "category"), ({"email_mode": "FULL"}, "email_mode"), ({"email_mode": True}, "email_mode")):
        r, _ = send(nt, pid, "x", "y", **body)
        check(f"invalid {field} {body[field]!r} → 400", r.status_code == 400 and r.get_json()["error"]["details"][0]["field"] == field, r.get_json())

    # ================================================================ safety of the content
    r, d = send(nt, pid, "<b>行程</b>\r\nBcc: x@evil.test", "請看 <script>alert(1)</script>\nhttps://evil.example/login 與 http://a.test",
                category="schedule", email_mode="full")
    m = emails()[-1]
    html = m["html"]
    check("subject is one line (no CR / LF header injection)", "\r" not in m["subject"] and "\n" not in m["subject"], repr(m["subject"]))
    check("HTML escapes the title and content", "<script>" not in html and "&lt;script&gt;" in html and "&lt;b&gt;" in html)
    check("nurse URLs are not links (broken up against auto-linking)", 'href="https://evil' not in html and "https:/​/evil.example" in html)
    check("the only link is the system sign-in button", html.count("<a ") == 1 and f'href="{app.config["APP_BASE_URL"]}/patient/notifications"' in html)
    leaks = [x for m in emails() for x in forbidden_values if x in m["text"] or x in m["subject"] or x in (m["html"] or "")]
    check("no patient code / ids / login email / password / token in any email", not leaks, leaks)

    # ================================================================ conditions unchanged
    c.patch("/api/v1/patients/me/profile", json={"email_notification_enabled": False}, headers=H(pt))
    before = len(emails())
    r, d = send(nt, pid, "行程", "內容", category="schedule", email_mode="full")
    check("full requested but patient switched email off → skipped / disabled", d["email_delivery"]["skip_reason"] == "disabled" and len(emails()) == before)
    c.patch("/api/v1/patients/me/profile", json={"email_notification_enabled": True}, headers=H(pt))
    when = (utcnow() + timedelta(hours=3)).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    r, d = send(nt, pid, "排程", "內容", category="schedule", email_mode="full", scheduled_for=when)
    check("scheduled + full → skipped / scheduled (no scheduler)", d["email_delivery"]["skip_reason"] == "scheduled" and d["email_delivery"]["mode"] == "full")
    r, d = send(nt, p2.public_id, "行程", "內容", category="schedule", email_mode="full")
    check("non-assigned patient → 404 (authorization unchanged)", r.status_code == 404)
    check("patient cannot send → 403", send(pt, pid, "x", "y")[0].status_code == 403)

    # ================================================================ 8. failure does not roll back
    verify_contact(pt, p1.user_id, "patient+fail@example.test")
    before_n = db.session.query(Notification).count()
    r, d = send(nt, pid, "行程", "明天 9 點", category="schedule", email_mode="full")
    check("full email rejected → still 201, notification kept, delivery failed", r.status_code == 201 and d["email_delivery"]["status"] == "failed"
          and d["email_delivery"]["mode"] == "full" and db.session.query(Notification).count() == before_n + 1 and d["status"] == "new")
    verify_contact(pt, p1.user_id, "patient.one@example.test")

    # ================================================================ not configured / replay
    app.extensions["email_service"] = DisabledEmailService(app.config)
    r, d = send(nt, pid, "行程", "內容", category="schedule", email_mode="full")
    check("no provider → skipped / not_configured (mode still recorded)", d["email_delivery"]["skip_reason"] == "not_configured" and d["email_delivery"]["mode"] == "full")
    app.extensions["email_service"] = service
    key = str(uuid.uuid4())
    before = len(emails())
    r1, d1 = send(nt, pid, "行程", "內容", key=key, category="schedule", email_mode="full")
    r2, d2 = send(nt, pid, "行程", "內容", key=key, category="schedule", email_mode="full")
    check("replay → same notification, one email", d1["id"] == d2["id"] and len(emails()) == before + 1 and d2["email_delivery"]["mode"] == "full")

    # ================================================================ 11/12/13. unchanged parts
    r = c.post(f"{URL}/{d1['id']}/acknowledge", headers=H(nt))
    r = c.post(f"{URL}/{d1['id']}/start", headers=H(nt))
    r = c.post(f"{URL}/{d1['id']}/resolve", json={"resolution_note": "已確認"}, headers=H(nt))
    check("lifecycle new → acknowledged → in_progress → resolved unchanged", r.status_code == 200 and r.get_json()["data"]["status"] == "resolved")
    check("state machine definition unchanged", TRANSITIONS == {
        "acknowledge": (("new",), "acknowledged"), "start": (("acknowledged",), "in_progress"), "resolve": (("in_progress",), "resolved")})
    before = len(emails())
    r = c.post("/api/v1/vital-signs", json={"patient_id": "me", "temperature_c": 39.3}, headers=H(pt))
    alerts = db.session.query(Notification).filter_by(source_table="vital_signs", source_id=r.get_json()["data"]["id"]).all()
    check("risk alerts are still never emailed", alerts and len(emails()) == before
          and db.session.query(NotificationDelivery).filter(NotificationDelivery.notification_id.in_([a.id for a in alerts])).count() == 0)
    v = [m for m in outbox if m["purpose"] == "verification"][-1]
    check("verification email unchanged (link + 24 h), now named 癌症照護系統", "/patient/verify-email#token=" in v["text"]
          and "24 小時" in v["text"] and "癌症照護系統" in v["subject"] and v["html"] is None)
    blob = json.dumps([[a.changes, a.reason] for a in db.session.query(AuditLog).all()], ensure_ascii=False)
    check("audit holds no email body / address / content", all(x not in blob for x in ("example.test", "ANC 為 0.8", "明天早上 6 點後請空腹", "evil.example")))

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
