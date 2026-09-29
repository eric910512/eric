"""Sprint 5: corrections of symptom records, vital signs and lab results (amend / mark-error,
history, original never overwritten, cycle kept), alerts of corrected records, cross-patient
review queues (pending symptom reviews, abnormal labs), dashboard / risk / timeline using the
effective data, permissions, idempotency, audit."""
import sys
from pathlib import Path
import json
import uuid
import warnings
from datetime import timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from app import create_app
from app.extensions import db
from app.models import AuditLog, LabResult, Notification, PatientProfile, SymptomRecord, User, VitalSign
from app.models.base import utcnow
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()


def token(email, password="Demo@1234"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password}).get_json()["data"]["access_token"]


def H(t, key=None):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": key or str(uuid.uuid4())}


def err(r):
    j = r.get_json() or {}
    return r.status_code, j.get("error", {}).get("code"), [d.get("field") for d in j.get("error", {}).get("details", [])]


def iso(dt):
    return dt.replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def open_alerts(table, rid):
    return db.session.query(Notification).filter(Notification.source_table == table, Notification.source_id == rid,
                                                 Notification.status != "resolved").all()


def symptom(pain=2, fever=False):
    return {"patient_id": "me", "form_code": "daily_chemo_check", "values": [
        {"definition_code": "pain", "value_numeric": pain}, {"definition_code": "nausea", "value_numeric": 1},
        {"definition_code": "fatigue", "value_numeric": 1}, {"definition_code": "fever", "value_boolean": fever}]}


with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()
    at, nt, pt = token("admin01@demo.local"), token("nurse01@demo.local"), token("patient01@demo.local")
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    P1 = p1.public_id
    other = User(role=nurse.role, email="n9@demo.local", password_hash=nurse.password_hash, display_name="其他護理師", password_changed_at=nurse.password_changed_at)
    db.session.add(other)
    db.session.commit()
    ot = token("n9@demo.local")

    # ================================================================ vital: typo fever → corrected
    v = c.post("/api/v1/vital-signs", json={"patient_id": "me", "temperature_c": 39.1, "heart_rate_bpm": 88}, headers=H(pt)).get_json()["data"]
    fever_alerts = open_alerts("vital_signs", v["id"])
    check("setup: typo 39.1 °C raised a fever alert", fever_alerts and v["triggered_alerts"])
    check("patient cannot amend → 403", c.post(f"/api/v1/vital-signs/{v['id']}/amend", json={"amend_reason": "x", "temperature_c": 36.8}, headers=H(pt)).status_code == 403)
    check("admin cannot amend → 403", c.post(f"/api/v1/vital-signs/{v['id']}/amend", json={"amend_reason": "x", "temperature_c": 36.8}, headers=H(at)).status_code == 403)
    check("unassigned nurse → 404", c.post(f"/api/v1/vital-signs/{v['id']}/amend", json={"amend_reason": "x", "temperature_c": 36.8}, headers=H(ot)).status_code == 404)
    check("amend needs a reason", "amend_reason" in err(c.post(f"/api/v1/vital-signs/{v['id']}/amend", json={"temperature_c": 36.8}, headers=H(nt)))[2])
    check("nothing changed → 400", err(c.post(f"/api/v1/vital-signs/{v['id']}/amend", json={"amend_reason": "x"}, headers=H(nt)))[0] == 400)
    check("unknown field → 400", "patient_id" in err(c.post(f"/api/v1/vital-signs/{v['id']}/amend", json={"amend_reason": "x", "patient_id": P1}, headers=H(nt)))[2])
    check("invalid corrected value → 400 (same validation as a new reading)", err(c.post(f"/api/v1/vital-signs/{v['id']}/amend", json={"amend_reason": "x", "temperature_c": 50}, headers=H(nt)))[0] == 400)
    key = str(uuid.uuid4())
    r = c.post(f"/api/v1/vital-signs/{v['id']}/amend", json={"amend_reason": "輸入錯誤：應為 36.8", "temperature_c": 36.8}, headers=H(nt, key))
    nv_ = r.get_json()["data"]
    r2 = c.post(f"/api/v1/vital-signs/{v['id']}/amend", json={"amend_reason": "輸入錯誤：應為 36.8", "temperature_c": 36.8}, headers=H(nt, key))
    db.session.expire_all()
    orig = db.session.get(VitalSign, v["id"])
    new = db.session.get(VitalSign, nv_["id"])
    check("amend → new record (36.8, same time, HR kept), original amended, not overwritten", r.status_code == 201 and new.amends_id == orig.id
          and float(new.temperature_c) == 36.8 and new.heart_rate_bpm == 88 and new.measured_at == orig.measured_at
          and orig.record_status == "amended" and float(orig.temperature_c) == 39.1)
    check("same key → replay, one correction only", r2.get_json()["data"]["id"] == nv_["id"] and db.session.query(VitalSign).filter_by(amends_id=orig.id).count() == 1)
    check("author / source / cycle kept from the original", new.recorded_by == orig.recorded_by and new.source == "patient_app"
          and new.cycle_id == orig.cycle_id and new.cycle_day == orig.cycle_day)
    check("original's fever alert resolved (note says why); no new alert", not open_alerts("vital_signs", orig.id) and not open_alerts("vital_signs", new.id)
          and all("已不符合" in n.resolution_note for n in db.session.query(Notification).filter_by(source_table="vital_signs", source_id=orig.id)))
    lv = c.get("/api/v1/dashboard/patient/me", headers=H(pt)).get_json()["data"]["widgets"]["latest-vitals"]
    check("dashboard uses the corrected reading", lv["temperature_c"]["value"] == 36.8)
    risk = c.get(f"/api/v1/dashboard/patient/{P1}", headers=H(nt)).get_json()["data"]["widgets"]["nurse-view"]["risk"]
    check("risk engine: no fever reason after the correction", not any("發燒" in x or "39.1" in x for x in risk["reasons"]), risk["reasons"])
    tl = [e["source_id"] for e in c.get("/api/v1/patients/me/timeline", headers=H(pt)).get_json()["data"] if e["event_type"] == "VITAL_SIGN"]
    check("timeline shows the corrected reading only", new.id in tl and orig.id not in tl)
    hist = c.get(f"/api/v1/vital-signs/{new.id}/history", headers=H(nt)).get_json()["data"]
    check("history: original → correction with values and status", [h["id"] for h in hist] == [orig.id, new.id]
          and [h["record_status"] for h in hist] == ["amended", "final"] and hist[0]["temperature_c"] == 39.1 and hist[0]["amended_by_id"] == new.id)
    check("history: admin can read, patient cannot", c.get(f"/api/v1/vital-signs/{new.id}/history", headers=H(at)).status_code == 200
          and c.get(f"/api/v1/vital-signs/{new.id}/history", headers=H(pt)).status_code == 403)
    check("amending the amended original → 409", c.post(f"/api/v1/vital-signs/{orig.id}/amend", json={"amend_reason": "x", "temperature_c": 37}, headers=H(nt)).status_code == 409)

    # still febrile after correction: the alert is not lost (fresh patient: no cooldown from earlier alerts)
    fresh = c.post("/api/v1/patients", json={"display_name": "修正測試", "gender": "male", "date_of_birth": "1960-01-01"}, headers=H(at)).get_json()["data"]
    c.post(f"/api/v1/patients/{fresh['id']}/nurse-assignments", json={"nurse_id": nurse.public_id}, headers=H(at))
    v2 = c.post("/api/v1/vital-signs", json={"patient_id": fresh["id"], "temperature_c": 39.5}, headers=H(nt)).get_json()["data"]
    check("setup: fresh patient 39.5 °C raised an alert", bool(open_alerts("vital_signs", v2["id"])))
    r = c.post(f"/api/v1/vital-signs/{v2['id']}/amend", json={"amend_reason": "耳溫重量", "temperature_c": 38.9}, headers=H(nt))
    kept = open_alerts("vital_signs", v2["id"]) + open_alerts("vital_signs", r.get_json()["data"]["id"])
    check("correction still febrile → a fever alert stays open (original kept, or re-raised for the correction)", len({n.event_key for n in kept}) >= 1,
          [(n.source_id, n.status) for n in kept])

    # ================================================================ symptom: pain 9 typo → 3
    s1 = c.post("/api/v1/symptoms/records", json=symptom(pain=9), headers=H(pt)).get_json()["data"]
    check("setup: pain 9 raised severe_pain", open_alerts("symptom_records", s1["id"]))
    pend = c.get("/api/v1/dashboard/widgets/pending-symptom-reviews/data", headers=H(nt)).get_json()
    check("pending reviews (cross-patient) lists it with patient and open alerts", any(i["id"] == s1["id"] and i["patient"]["patient_code"] == "P00001"
                                                                                      and i["open_alert_count"] >= 1 for i in pend["data"]))
    check("pending list is nurse only", c.get("/api/v1/dashboard/widgets/pending-symptom-reviews/data", headers=H(pt)).status_code == 403)
    body = {"amend_reason": "病人按錯", "values": [{"definition_code": "pain", "value_numeric": 3}, {"definition_code": "nausea", "value_numeric": 1},
                                                  {"definition_code": "fatigue", "value_numeric": 1}, {"definition_code": "fever", "value_boolean": False}]}
    check("invalid corrected answers → 400", err(c.post(f"/api/v1/symptoms/records/{s1['id']}/amend", json={**body, "values": [{"definition_code": "pain", "value_numeric": 42}]}, headers=H(nt)))[0] == 400)
    r = c.post(f"/api/v1/symptoms/records/{s1['id']}/amend", json=body, headers=H(nt))
    s1b = r.get_json()["data"]
    db.session.expire_all()
    o1 = db.session.get(SymptomRecord, s1["id"])
    n1 = db.session.get(SymptomRecord, s1b["id"])
    check("symptom amend → new record, same time / cycle / reporter, still submitted", r.status_code == 201 and n1.amends_id == o1.id
          and n1.recorded_at == o1.recorded_at and n1.cycle_day == o1.cycle_day and n1.reported_by == o1.reported_by
          and n1.review_status == "submitted" and o1.record_status == "amended")
    check("severe_pain alert of the original resolved", not open_alerts("symptom_records", o1.id))
    pend = [i["id"] for i in c.get("/api/v1/dashboard/widgets/pending-symptom-reviews/data", headers=H(nt)).get_json()["data"]]
    check("pending list shows the corrected record instead of the original", n1.id in pend and o1.id not in pend)
    recs = [x["id"] for x in c.get("/api/v1/symptoms/records/me?review_status=all", headers=H(pt)).get_json()["data"]]
    check("patient's record list shows the corrected record only", n1.id in recs and o1.id not in recs)
    s2 = c.post("/api/v1/symptoms/records", json=symptom(fever=True), headers=H(pt)).get_json()["data"]
    check("mark-error needs a reason", "reason" in err(c.post(f"/api/v1/symptoms/records/{s2['id']}/mark-error", json={}, headers=H(nt)))[2])
    r = c.post(f"/api/v1/symptoms/records/{s2['id']}/mark-error", json={"reason": "填寫到別人的"}, headers=H(nt))
    check("mark-error → entered_in_error, its alerts resolved and reported", r.get_json()["data"]["record_status"] == "entered_in_error"
          and r.get_json()["data"]["closed_alerts"] and not open_alerts("symptom_records", s2["id"]))
    check("erroneous record gone from pending list and timeline", s2["id"] not in [i["id"] for i in c.get("/api/v1/dashboard/widgets/pending-symptom-reviews/data", headers=H(nt)).get_json()["data"]]
          and all(not (e["event_type"] == "SYMPTOM" and e["source_id"] == s2["id"]) for e in c.get(f"/api/v1/patients/{P1}/timeline", headers=H(nt)).get_json()["data"]))
    check("review of an erroneous record → rejected", c.post(f"/api/v1/symptoms/records/{s2['id']}/review", json={"action_note": "x"}, headers=H(nt)).status_code in (409, 422))

    seed_rec = db.session.query(SymptomRecord).filter_by(patient_id=p1.id, record_status="final").order_by(SymptomRecord.recorded_at).first()
    r = c.post(f"/api/v1/symptoms/records/{seed_rec.id}/amend", json={"amend_reason": "舊紀錄更正", "values": [
        {"definition_code": "pain", "value_numeric": 1}, {"definition_code": "nausea", "value_numeric": 1}, {"definition_code": "fatigue", "value_numeric": 1}]}, headers=H(nt))
    check("an old report (before a question became required) can still be corrected", r.status_code == 201, r.get_json())

    # ================================================================ lab: ANC typo → corrected
    lab = c.post("/api/v1/labs/results", json={"patient_id": P1, "collected_at": iso(utcnow() - timedelta(hours=1)),
                                                 "results": [{"test_code": "ANC", "value": 0.4}, {"test_code": "WBC", "value": 5.0}]}, headers=H(nt)).get_json()["data"]
    anc = next(x for x in lab["results"] if x["test_code"] == "ANC")
    ab = c.get("/api/v1/labs/abnormal", headers=H(nt)).get_json()
    check("abnormal labs list (cross-patient) shows ANC 0.4", any(i["id"] == anc["id"] and i["patient"]["patient_code"] == "P00001" for i in ab["data"])
          and ab["meta"]["total"] >= 1)
    check("abnormal labs list is nurse only", c.get("/api/v1/labs/abnormal", headers=H(pt)).status_code == 403)
    had_alert = bool(open_alerts("lab_results", anc["id"]))
    r = c.post(f"/api/v1/labs/results/{anc['id']}/amend", json={"amend_reason": "小數點誤植", "value": 4.0}, headers=H(nt))
    anc2 = r.get_json()["data"]
    db.session.expire_all()
    check("lab amend → new row 4.0, flag N, original amended; the other analyte untouched", r.status_code == 201 and anc2["value"] in ("4.0", "4.00", 4.0)
          and anc2["abnormal_flag"] == "N" and db.session.get(LabResult, anc["id"]).record_status == "amended"
          and db.session.get(LabResult, next(x for x in lab["results"] if x["test_code"] == "WBC")["id"]).record_status == "final", anc2)
    check("ANC alert of the original resolved" if had_alert else "no ANC alert to resolve (rule not met)", not open_alerts("lab_results", anc["id"]))
    check("abnormal list no longer shows the corrected-away value", anc["id"] not in [i["id"] for i in c.get("/api/v1/labs/abnormal", headers=H(nt)).get_json()["data"]])
    summary = c.get("/api/v1/labs/summary/me", headers=H(pt)).get_json()["data"]
    check("patient lab summary uses the corrected ANC", next(i for i in summary["items"] if i["code"] == "ANC")["status"] == "normal", summary["items"])
    hist = c.get(f"/api/v1/labs/results/{anc2['id']}/history", headers=H(nt)).get_json()["data"]
    check("lab history chain", [h["id"] for h in hist] == [anc["id"], anc2["id"]] and hist[0]["abnormal_flag"] in ("LL", "L"))

    # ================================================================ cycle kept across a new cycle
    seed_vital = db.session.query(VitalSign).filter_by(patient_id=p1.id, record_status="final").order_by(VitalSign.measured_at).first()
    plan = c.get(f"/api/v1/chemotherapy/plans?patient_id={P1}", headers=H(nt)).get_json()["data"][0]
    today = (utcnow() + timedelta(hours=8)).date()
    c.post(f"/api/v1/chemotherapy/cycles/{plan['cycles'][0]['id']}/complete", json={"end_date": (today - timedelta(days=1)).isoformat()}, headers=H(nt))
    c2 = c.post(f"/api/v1/chemotherapy/plans/{plan['id']}/cycles", json={"scheduled_date": today.isoformat()}, headers=H(nt)).get_json()["data"]
    c.post(f"/api/v1/chemotherapy/cycles/{c2['id']}/start", json={}, headers=H(nt))
    before = (seed_vital.cycle_id, seed_vital.cycle_day)
    r = c.post(f"/api/v1/vital-signs/{seed_vital.id}/amend", json={"amend_reason": "體重單位誤植", "weight_kg": 63.2}, headers=H(nt))
    db.session.expire_all()
    fixed = db.session.get(VitalSign, r.get_json()["data"]["id"])
    check("correcting a cycle-1 reading after cycle 2 started keeps cycle 1 and its day", r.status_code == 201
          and (fixed.cycle_id, fixed.cycle_day) == before and before[0] != c2["id"], (before, fixed.cycle_id, fixed.cycle_day))

    # ================================================================ audit
    logs = db.session.query(AuditLog).filter(AuditLog.action.in_(["AMEND", "MARK_ERROR"])).all()
    amend_v = next(l for l in logs if l.resource_type == "vital_signs" and l.resource_id == str(v["id"]))
    check("audit: AMEND with reason, new id, fields and closed alerts", amend_v.changes["amend_reason"] == "輸入錯誤：應為 36.8"
          and amend_v.changes["new_record_id"] == nv_["id"] and amend_v.changes["fields"] == ["temperature_c"] and amend_v.changes["closed_alerts"]
          and amend_v.actor_user_id == nurse.id)
    check("audit: one AMEND per correction (replay not logged twice)", len([l for l in logs if l.resource_type == "vital_signs" and l.resource_id == str(v["id"])]) == 1)
    check("audit: MARK_ERROR with reason", any(l.action == "MARK_ERROR" and l.changes["reason"] == "填寫到別人的" for l in logs))
    check("audit: symptom and lab corrections", {l.resource_type for l in logs} >= {"vital_signs", "symptom_records", "lab_results"})
    check("no password in audit", "Demo@1234" not in json.dumps([l.changes for l in db.session.query(AuditLog).all()], ensure_ascii=False))

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
