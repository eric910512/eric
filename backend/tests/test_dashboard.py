"""Patient dashboard endpoint: envelope, widgets, validation, record filtering.

Ported from the pre-authentication API Sprint 1 test: requests now carry a JWT (the old
DEV_AUTH_BYPASS no longer exists), the widget list is the current one.
"""
import sys
from pathlib import Path
import warnings
from datetime import timedelta
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from app import create_app
from app.extensions import db
from app.models import AuditLog, Notification, NursingAssessment, PatientProfile, User, VitalSign
from app.models.base import utcnow
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
client = app.test_client()


def token(email):
    return client.post("/api/v1/auth/login", json={"email": email, "password": "Demo@1234"}).get_json()["data"]["access_token"]


with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()
    p = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    pid = p.public_id
    url = f"/api/v1/dashboard/patient/{pid}"
    NT = {"Authorization": f"Bearer {token('nurse01@demo.local')}"}
    PT = {"Authorization": f"Bearer {token('patient01@demo.local')}"}

    r = client.get(url, headers={**NT, "X-Request-ID": "test-req-1"})
    body = r.get_json()
    w = body["data"]["widgets"]
    check("200 + envelope {data, meta.timezone}", r.status_code == 200 and body["meta"] == {"timezone": "Asia/Taipei"})
    check("X-Request-ID echoed", r.headers.get("X-Request-ID") == "test-req-1")
    check("nurse: all widget keys present, in order (nurse-view last)",
          list(w) == ["patient-summary", "risk-summary", "today-schedule", "treatment-progress", "latest-vitals",
                      "lab-summary", "symptom-trend", "symptom-quick-report", "notifications", "nurse-view"], list(w))
    pw = client.get("/api/v1/dashboard/patient/me", headers=PT).get_json()["data"]["widgets"]
    check("patient: same widgets without nurse-view", list(pw) == list(w)[:-1], list(pw))
    check("integers stay integers", w["latest-vitals"]["heart_rate_bpm"]["value"] == 88
          and isinstance(w["latest-vitals"]["heart_rate_bpm"]["value"], int))
    check("trend_days=7 → 7 points per series",
          all(len(s["points"]) == 7 for s in client.get(url + "?trend_days=7", headers=NT).get_json()["data"]["widgets"]["symptom-trend"]["series"]))

    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    audits = db.session.query(AuditLog).filter_by(patient_id=p.id, action="VIEW", resource_type="dashboard").order_by(AuditLog.id).all()
    check("audit VIEW written per call with the real actor and request id",
          len(audits) == 3 and audits[0].actor_user_id == nurse.id and audits[0].request_id == "test-req-1")

    # ---- error paths ----
    def err(path, status, code, headers=NT):
        resp = client.get(path, headers=headers)
        j = resp.get_json()
        good = resp.status_code == status and j and j.get("error", {}).get("code") == code and j["error"].get("request_id")
        check(f"{path.split('/api/v1')[1][:60]} → {status} {code}", good, resp.status_code)

    err("/api/v1/dashboard/patient/not-a-uuid", 404, "NOT_FOUND")
    err("/api/v1/dashboard/patient/00000000-0000-0000-0000-000000000000", 404, "NOT_FOUND")
    err("/api/v1/dashboard/patient/me", 404, "NOT_FOUND")  # 'me' is only for patient accounts
    err(url, 401, "UNAUTHENTICATED", headers={})
    err(url + "?trend_days=abc", 400, "VALIDATION_ERROR")
    err(url + "?trend_days=0", 400, "VALIDATION_ERROR")
    err(url + "?trend_days=91", 400, "VALIDATION_ERROR")
    err("/api/v1/nope", 404, "NOT_FOUND")
    check("POST → 405 METHOD_NOT_ALLOWED", client.post(url, headers=NT).status_code == 405)

    # ---- risk / widget rules ----
    cycle = p.chemotherapy_cycles[0]
    pu = p.user
    db.session.add(VitalSign(patient=p, cycle=cycle, cycle_day=4, measured_at=utcnow() - timedelta(minutes=10),
                             temperature_c=Decimal("38.3"), heart_rate_bpm=112, recorder=pu, source="patient_app"))
    db.session.commit()
    risk = client.get(url, headers=NT).get_json()["data"]["widgets"]["nurse-view"]["risk"]
    check("critical temperature → risk high", risk["level"] == "high", risk["reasons"])

    db.session.add_all([
        Notification(recipient=nurse, patient=p, event_key="vital:x:rule:1", type="risk_alert", severity="critical",
                     title="疑似發燒", message="T 38.3"),
        Notification(recipient=pu, patient=p, event_key="vital:x:rule:1", type="risk_alert", severity="critical",
                     title="疑似發燒", message="請聯絡醫療團隊"),
        Notification(recipient=pu, patient=p, type="reminder", severity="info", title="未來提醒", message="x",
                     scheduled_for=utcnow() + timedelta(days=1)),
        NursingAssessment(patient=p, assessor=nurse, assessed_at=utcnow(), assessment_type="follow_up",
                          risk_level="medium", source="nurse"),
    ])
    db.session.commit()
    nv = client.get(url, headers=NT).get_json()["data"]
    alerts = nv["widgets"]["nurse-view"]["unacknowledged_alerts"]
    check("same event_key for 2 recipients shown once", alerts["count"] == 1, alerts["items"][0]["title"])
    check("pending assessment signoff listed", nv["widgets"]["nurse-view"]["pending_assessment_signoff"]["count"] == 1)
    check("future scheduled reminder hidden from patient notifications",
          all(i["title"] != "未來提醒" for i in nv["widgets"]["notifications"]["items"]),
          f"unread={nv['widgets']['notifications']['unread_count']}")
    check("latest-vitals picks newest value per field",
          nv["widgets"]["latest-vitals"]["temperature_c"]["value"] == 38.3
          and nv["widgets"]["latest-vitals"]["temperature_c"]["flag"] == "critical"
          and nv["widgets"]["latest-vitals"]["spo2_pct"]["value"] == 98)

    # amended / entered-in-error vitals are ignored
    v = db.session.query(VitalSign).filter_by(temperature_c=Decimal("38.3")).one()
    v.record_status = "entered_in_error"
    db.session.commit()
    lv = client.get(url, headers=NT).get_json()["data"]["widgets"]["latest-vitals"]
    check("entered_in_error vitals excluded", lv["temperature_c"]["value"] == 37.2)

    # soft-deleted patient → 404
    p.deleted_at = utcnow()
    db.session.commit()
    check("soft-deleted patient → 404", client.get(url, headers=NT).status_code == 404)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
