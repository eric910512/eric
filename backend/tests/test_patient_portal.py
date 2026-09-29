"""Sprint 6: patient-side features — unread count, mark read, read all (own notifications only,
scheduled ones excluded), notification detail privacy, own profile, password change, and the
patient's own symptom list."""
import sys
from pathlib import Path
import json
import uuid
import warnings
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from app import create_app
from app.extensions import db
from app.models import AuditLog, Notification, PatientProfile, User
from app.models.base import utcnow
from app.models.enums import NotificationType
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()


def token(email, password="Demo@1234"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password}).get_json()["data"]["access_token"]


def H(t):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": str(uuid.uuid4())}


with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()
    nt, pt = token("nurse01@demo.local"), token("patient01@demo.local")
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()

    # an alert (patient + nurse copies), and a future reminder that must stay invisible
    c.post("/api/v1/vital-signs", json={"patient_id": "me", "temperature_c": 38.8}, headers=H(pt))
    db.session.add(Notification(recipient_id=p1.user_id, patient_id=p1.id, type=NotificationType.REMINDER, severity="info",
                                title="明天的提醒", message="明天記得空腹", scheduled_for=utcnow() + timedelta(days=1)))
    db.session.commit()
    nurse_unread_before = c.get("/api/v1/notifications/unread-count", headers=H(nt)).get_json()["data"]["unread"]

    # ================================================================ unread count / list
    u = c.get("/api/v1/notifications/unread-count", headers=H(pt)).get_json()["data"]["unread"]
    lst = c.get("/api/v1/notifications?per_page=100", headers=H(pt)).get_json()
    check("unread count = own unread visible notifications (seed reminder + fever alert)", u == lst["meta"]["unread"] and u >= 2, (u, lst["meta"]["unread"]))
    check("future reminder not listed yet", all(n["title"] != "明天的提醒" for n in lst["data"]))
    check("patient list holds only own notifications", all(n.get("patient", {"id": p1.public_id})["id"] == p1.public_id for n in lst["data"] if n.get("patient")))
    alert = next(n for n in lst["data"] if n["type"] == "risk_alert")
    d = c.get(f"/api/v1/notifications/{alert['id']}", headers=H(pt)).get_json()["data"]
    check("patient detail: no nurse names / internal note / trigger", "resolution_note" not in json.dumps(d) and not {"trigger", "source_record", "recipients", "allowed_actions"} & set(d)
          and "測試護理師" not in json.dumps(d, ensure_ascii=False), sorted(d))
    nurse_copy = db.session.query(Notification).filter_by(recipient_id=nurse.id).first()
    check("patient cannot open a nurse's copy → 404", c.get(f"/api/v1/notifications/{nurse_copy.id}", headers=H(pt)).status_code == 404
          and c.patch(f"/api/v1/notifications/{nurse_copy.id}/read", headers=H(pt)).status_code == 404)

    # ================================================================ read one / read all
    r = c.patch(f"/api/v1/notifications/{alert['id']}/read", headers=H(pt))
    check("mark one read", r.get_json()["data"]["is_read"] is True and c.get("/api/v1/notifications/unread-count", headers=H(pt)).get_json()["data"]["unread"] == u - 1)
    r = c.post("/api/v1/notifications/read-all", headers=H(pt))
    check("read all → updated count, unread 0", r.status_code == 200 and r.get_json()["data"]["updated"] == u - 1
          and c.get("/api/v1/notifications/unread-count", headers=H(pt)).get_json()["data"]["unread"] == 0)
    check("read all again → nothing to update", c.post("/api/v1/notifications/read-all", headers=H(pt)).get_json()["data"]["updated"] == 0)
    future = db.session.query(Notification).filter_by(title="明天的提醒").one()
    check("the future reminder stays unread (not visible yet)", future.is_read is not True)
    check("the nurse's copies are untouched", c.get("/api/v1/notifications/unread-count", headers=H(nt)).get_json()["data"]["unread"] == nurse_unread_before)
    check("read-all audited without contents", any(l.changes and l.changes.get("read_all") for l in db.session.query(AuditLog).filter_by(resource_type="notifications").all()))
    check("unread-count needs sign-in", c.get("/api/v1/notifications/unread-count").status_code == 401)

    # ================================================================ profile / records / password
    me = c.get("/api/v1/patients/me", headers=H(pt)).get_json()["data"]
    check("own profile without care team / account / creator", me["patient_code"] == "P00001" and not {"care_team", "account", "created_by"} & set(me))
    recs = c.get("/api/v1/symptoms/records/me?review_status=all", headers=H(pt)).get_json()["data"]
    check("own symptom records, reviewer names hidden", recs and all("reviewed_by" not in r or r["reviewed_by"] is None or isinstance(r["reviewed_by"], bool) for r in recs)
          and "測試護理師" not in json.dumps(recs, ensure_ascii=False))
    check("password change: wrong current → 400; ok → 204; new password works",
          c.put("/api/v1/auth/password", json={"current_password": "nope", "new_password": "Patient2026a"}, headers=H(pt)).status_code == 400
          and c.put("/api/v1/auth/password", json={"current_password": "Demo@1234", "new_password": "Patient2026a"}, headers=H(pt)).status_code == 204
          and c.post("/api/v1/auth/login", json={"email": "patient01@demo.local", "password": "Patient2026a"}).status_code == 200)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
