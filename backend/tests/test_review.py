import sys
from pathlib import Path
import uuid
import warnings
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import (AuditLog, Notification, NursePatientAssignment, NursingAssessment, PatientProfile, Role,
                        SymptomRecord, SymptomRecordValue, User)
from app.models.base import utcnow
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()


def token(email):
    return c.post("/api/v1/auth/login", json={"email": email, "password": "Demo@1234"}).get_json()["data"]["access_token"]


def H(t):
    return {"Authorization": f"Bearer {t}"}


def submit(t, pain=2, fever=False):
    body = {"patient_id": "me", "form_code": "daily_chemo_check", "values": [
        {"definition_code": "pain", "value_numeric": pain}, {"definition_code": "nausea", "value_numeric": 3},
        {"definition_code": "fatigue", "value_numeric": 4}, {"definition_code": "fever", "value_boolean": fever}]}
    return c.post("/api/v1/symptoms/records", json=body, headers={**H(t), "Idempotency-Key": str(uuid.uuid4())}).get_json()["data"]


def code(resp):
    j = resp.get_json() or {}
    return resp.status_code, j.get("error", {}).get("code")


with app.app_context():
    db.create_all()
    seed_dev_data()
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    nurse2 = User(role=roles["nurse"], email="nurse02@demo.local", password_hash=pw, display_name="測試護理師 陳", password_changed_at=utcnow())
    db.session.add(nurse2)
    db.session.commit()
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    # second nurse also assigned → an event has 3 recipients
    db.session.add(NursePatientAssignment(nurse_id=nurse2.id, patient_id=p1.id))
    db.session.commit()
    pt, nt, nt2 = token("patient01@demo.local"), token("nurse01@demo.local"), token("nurse02@demo.local")

    rec = submit(pt, pain=8, fever=True)          # severe_pain + reported_fever
    rec_ok = submit(pt, pain=2)                   # no alerts

    # ---------------- GET /notifications ----------------
    r = c.get("/api/v1/notifications?status=unresolved", headers=H(nt))
    body = r.get_json()
    check("nurse unresolved list: 2 risk alerts (fever first by time desc), meta counts",
          r.status_code == 200 and len(body["data"]) == 2 and body["meta"]["unresolved"] == 2 and body["meta"]["unread"] == 2,
          [(n["title"], n["severity"]) for n in body["data"]])
    n0 = body["data"][0]
    check("payload has patient, rule, source, read/ack state",
          n0["patient"]["patient_code"] == "P00001" and n0["source"] == {"table": "symptom_records", "id": rec["id"]}
          and n0["alert_rule"]["code"] in ("reported_fever", "severe_pain") and n0["acknowledged"] is None)
    check("filters: severity=critical → 1", len(c.get("/api/v1/notifications?severity=critical", headers=H(nt)).get_json()["data"]) == 1)
    check("filters: patient_id", len(c.get(f"/api/v1/notifications?patient_id={p1.public_id}", headers=H(nt)).get_json()["data"]) == 2)
    check("bad status → 400", code(c.get("/api/v1/notifications?status=nope", headers=H(nt))) == (400, "VALIDATION_ERROR"))
    check("bad per_page → 400", code(c.get("/api/v1/notifications?per_page=500", headers=H(nt)))[0] == 400)
    pat_list = c.get("/api/v1/notifications", headers=H(pt)).get_json()
    check("patient sees own copies (2 alerts + seed reminder)", pat_list["meta"]["total"] == 3, pat_list["meta"])
    check("no token → 401", c.get("/api/v1/notifications").status_code == 401)

    # ---------------- PATCH read ----------------
    nid = n0["id"]
    r = c.patch(f"/api/v1/notifications/{nid}/read", headers=H(nt))
    check("read → is_read true + read_at", r.status_code == 200 and r.get_json()["data"]["is_read"] and r.get_json()["data"]["read_at"])
    audits_before = db.session.query(AuditLog).filter_by(resource_type="notifications", action="UPDATE").count()
    c.patch(f"/api/v1/notifications/{nid}/read", headers=H(nt))
    check("read is idempotent (no extra audit row)",
          db.session.query(AuditLog).filter_by(resource_type="notifications", action="UPDATE").count() == audits_before == 1)
    check("someone else's notification → 404", c.patch(f"/api/v1/notifications/{nid}/read", headers=H(pt)).status_code == 404)

    # ---------------- PATCH resolve ----------------
    fever_nid = next(n["id"] for n in body["data"] if n["alert_rule"]["code"] == "reported_fever")
    check("resolve without note → 400", code(c.patch(f"/api/v1/notifications/{fever_nid}/resolve", json={}, headers=H(nt))) == (400, "VALIDATION_ERROR"))
    pat_fever = next(n["id"] for n in pat_list["data"] if n["alert_rule"] and n["alert_rule"]["code"] == "reported_fever")
    check("patient cannot resolve → 403", c.patch(f"/api/v1/notifications/{pat_fever}/resolve", json={"resolution_note": "x"}, headers=H(pt)).status_code == 403)
    r = c.patch(f"/api/v1/notifications/{fever_nid}/resolve", json={"resolution_note": "  已電話聯繫病人，建議立即至急診  "}, headers=H(nt))
    d = r.get_json()["data"]
    check("resolve 200: acknowledged by nurse with trimmed note; 2 other copies updated (patient + nurse2)",
          r.status_code == 200 and d["acknowledged"]["by"]["display_name"] == "測試護理師 林"
          and d["acknowledged"]["resolution_note"] == "已電話聯繫病人，建議立即至急診" and d["related_notifications_updated"] == 2)
    copies = db.session.query(Notification).filter_by(event_key=db.session.get(Notification, fever_nid).event_key).all()
    check("all 3 copies of the event share the acknowledgement", len(copies) == 3 and all(x.acknowledged_by == nurse.id for x in copies))
    n2_fever = next(x.id for x in copies if x.recipient_id == nurse2.id)
    r = c.patch(f"/api/v1/notifications/{n2_fever}/resolve", json={"resolution_note": "我也要處理"}, headers=H(nt2))
    check("second nurse resolving the same event → 422 with who handled it",
          code(r) == (422, "INVALID_STATE") and "測試護理師 林" in r.get_json()["error"]["message"])
    reminder = db.session.query(Notification).filter_by(type="reminder").first()
    reminder_for_nurse = Notification(recipient_id=nurse.id, patient_id=p1.id, type="reminder", severity="info", title="提醒", message="x")
    db.session.add(reminder_for_nurse)
    db.session.commit()
    check("resolving a reminder → 422", code(c.patch(f"/api/v1/notifications/{reminder_for_nurse.id}/resolve", json={"resolution_note": "x"}, headers=H(nt)))[0] == 422)
    check("ACKNOWLEDGE audited with notification ids",
          db.session.query(AuditLog).filter_by(action="ACKNOWLEDGE", resource_type="notifications", resource_id=str(fever_nid)).one().changes["notification_ids"])
    unresolved_after = c.get("/api/v1/notifications?status=unresolved", headers=H(nt)).get_json()
    resolved_after = c.get("/api/v1/notifications?status=resolved", headers=H(nt)).get_json()
    check("lists move: unresolved 1, resolved 1", unresolved_after["meta"]["total"] == 1 and resolved_after["meta"]["total"] == 1)

    # ---------------- GET /symptoms/records/{patient_id} ----------------
    r = c.get(f"/api/v1/symptoms/records/{p1.public_id}?review_status=submitted", headers=H(nt))
    body = r.get_json()
    check("nurse: submitted records incl. seed (2) + today (2) = 4",
          r.status_code == 200 and body["meta"]["total"] == 4 and body["meta"]["submitted"] == 4 and body["meta"]["reviewed"] == 0)
    item = next(x for x in body["data"] if x["id"] == rec["id"])
    check("record carries alerts with per-event resolved state",
          sorted((a["alert_rule_code"], a["resolved"]) for a in item["alerts"]) == [("reported_fever", True), ("severe_pain", False)]
          and item["reported_by"]["display_name"] == "測試病人 甲", item["alerts"])
    check("bad review_status → 400", c.get(f"/api/v1/symptoms/records/{p1.public_id}?review_status=x", headers=H(nt)).status_code == 400)
    check("patient 'me' works", c.get("/api/v1/symptoms/records/me", headers=H(pt)).status_code == 200)

    # ---------------- POST review ----------------
    url = f"/api/v1/symptoms/records/{rec['id']}/review"
    check("review without action_note → 400", code(c.post(url, json={}, headers=H(nt))) == (400, "VALIDATION_ERROR"))
    check("bad CTCAE grade → 400", code(c.post(url, json={"action_note": "x", "ctcae_grades": [{"definition_code": "pain", "ctcae_grade": 7}]}, headers=H(nt)))[0] == 400)
    check("grade for unanswered symptom → 400", code(c.post(url, json={"action_note": "x", "ctcae_grades": [{"definition_code": "headache", "ctcae_grade": 1}]}, headers=H(nt)))[0] == 400)
    check("patient cannot review → 403", c.post(url, json={"action_note": "x"}, headers=H(pt)).status_code == 403)
    check("unknown record → 404", c.post("/api/v1/symptoms/records/99999/review", json={"action_note": "x"}, headers=H(nt)).status_code == 404)

    r = c.post(url, json={"action_note": "已電話衛教止痛藥使用，明日追蹤", "risk_level": "medium",
                          "ctcae_grades": [{"definition_code": "pain", "ctcae_grade": 2}]}, headers=H(nt))
    d = r.get_json()["data"]
    check("review 200: reviewed_by/at + review block with action note",
          r.status_code == 200 and d["review_status"] == "reviewed" and d["reviewed_by"]["display_name"] == "測試護理師 林"
          and d["reviewed_at"] and d["review"]["action_note"] == "已電話衛教止痛藥使用，明日追蹤"
          and d["review"]["assessment_type"] == "phone_follow_up", d["review"])
    check("remaining open alert (severe_pain) resolved by the review", d["resolved_notifications"] == 3
          and all(a["resolved"] for a in d["alerts"]), d["alerts"])
    record = db.session.get(SymptomRecord, rec["id"])
    na = db.session.get(NursingAssessment, record.nursing_assessment_id)
    check("signed nursing assessment linked: plan=note, subjective summary, CTCAE, cycle day",
          na.sign_status == "signed" and na.signed_at and na.plan == "已電話衛教止痛藥使用，明日追蹤"
          and "疼痛 8" in na.subjective and "有發燒或畏寒" in na.subjective and "疼痛 G2" in na.assessment
          and na.cycle_day == 4 and na.risk_level == "medium" and na.assessed_by == nurse.id, na.subjective)
    check("CTCAE grade stored on the value",
          db.session.query(SymptomRecordValue).filter_by(symptom_record_id=rec["id"]).all()[0].ctcae_grade == 2)
    check("audit: CREATE assessment + UPDATE record + ACKNOWLEDGE",
          db.session.query(AuditLog).filter_by(action="CREATE", resource_type="nursing_assessments", resource_id=str(na.id)).count() == 1
          and db.session.query(AuditLog).filter_by(action="UPDATE", resource_type="symptom_records", resource_id=str(rec["id"])).one().changes["review_status"]["new"] == "reviewed"
          and db.session.query(AuditLog).filter(AuditLog.action == "ACKNOWLEDGE", AuditLog.changes.isnot(None)).count() == 2)
    check("second review → 422 INVALID_STATE", code(c.post(url, json={"action_note": "again"}, headers=H(nt2))) == (422, "INVALID_STATE"))

    r = c.post(f"/api/v1/symptoms/records/{rec_ok['id']}/review", json={"action_note": "無異常", "assessment_type": "follow_up", "resolve_alerts": False}, headers=H(nt))
    check("follow_up type accepted, resolve_alerts=false ok", r.status_code == 200 and r.get_json()["data"]["review"]["assessment_type"] == "follow_up")
    pat_view = c.get("/api/v1/symptoms/records/me?review_status=reviewed", headers=H(pt)).get_json()
    check("patient sees reviewed status but not the nurse's internal note or name",
          pat_view["meta"]["reviewed"] == 2 and all(x["review"] is None and x["reviewed_by"] is None and x["reviewed_at"] and all(a["resolved_by"] is None for a in x["alerts"]) for x in pat_view["data"]))

    # ---------------- dashboards reflect the workflow ----------------
    nv = c.get(f"/api/v1/dashboard/patient/{p1.public_id}", headers=H(nt)).get_json()["data"]["widgets"]["nurse-view"]
    check("nurse-view: 2 pending (seed), 0 open alerts, latest assessment medium",
          nv["pending_symptom_reviews"]["count"] == 2 and nv["unacknowledged_alerts"]["count"] == 0
          and nv["latest_assessment"]["risk_level"] is None, {"risk": nv["risk"], "latest": nv["latest_assessment"]})
    case = c.get("/api/v1/dashboard/widgets/caseload/data", headers=H(nt)).get_json()["data"][0]
    check("caseload: 0 unacknowledged alerts, 2 pending", case["unacknowledged_alert_count"] == 0 and case["pending_review_count"] == 2, case["risk_level"])

    # ---------------- assignment ended → no access ----------------
    a2 = db.session.query(NursePatientAssignment).filter_by(nurse_id=nurse2.id).one()
    a2.ended_at = utcnow()
    db.session.commit()
    open_for_n2 = db.session.query(Notification).filter(Notification.recipient_id == nurse2.id, Notification.acknowledged_at.is_(None)).first()
    check("nurse whose assignment ended cannot review / list records",
          c.get(f"/api/v1/symptoms/records/{p1.public_id}", headers=H(nt2)).status_code == 404
          and c.post("/api/v1/symptoms/records/1/review", json={"action_note": "x"}, headers=H(nt2)).status_code == 404)
    check("…nor resolve an old alert copy", open_for_n2 is None or
          c.patch(f"/api/v1/notifications/{open_for_n2.id}/resolve", json={"resolution_note": "x"}, headers=H(nt2)).status_code == 404)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
