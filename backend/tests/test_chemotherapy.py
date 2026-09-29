"""Sprint 2: chemotherapy plans, cycles and medication records — drugs / regimens, plan and
cycle lifecycle, medication idempotency, corrections, no cycle_day recalculation, dashboard /
timeline / risk integration, permissions, privacy, audit."""
import sys
from pathlib import Path
import json
import uuid
import warnings
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from app import create_app
from app.extensions import db
from app.models import (AuditLog, ChemotherapyCycle, LabResult, MedicationRecord, PatientProfile, SymptomRecord, User,
                        VitalSign)
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
    return {"Authorization": f"Bearer {t}", **({"Idempotency-Key": key} if key else {})}


def err(r):
    j = r.get_json() or {}
    return r.status_code, j.get("error", {}).get("code"), [d.get("field") for d in j.get("error", {}).get("details", [])]


def iso(dt):
    return dt.replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def snapshot(patient_id=None):
    rows = {}
    for m in (SymptomRecord, VitalSign, LabResult, MedicationRecord):
        q = db.session.query(m)
        if patient_id:
            q = q.filter_by(patient_id=patient_id)
        rows[m.__name__] = sorted((r.id, r.cycle_id, r.cycle_day) for r in q.all())
    return rows


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

    # ================================================================ drugs / regimens (admin writes)
    drugs = c.get("/api/v1/chemotherapy/drugs", headers=H(nt)).get_json()["data"]
    cis = next(d for d in drugs if d["generic_name"] == "Cisplatin")
    check("nurse lists drugs (seed Cisplatin)", cis["default_route"] == "IV")
    check("patient cannot list drugs → 403", c.get("/api/v1/chemotherapy/drugs", headers=H(pt)).status_code == 403)
    check("nurse cannot create drugs → 403", c.post("/api/v1/chemotherapy/drugs", json={"generic_name": "X"}, headers=H(nt)).status_code == 403)
    r = c.post("/api/v1/chemotherapy/drugs", json={"generic_name": "Fluorouracil", "brand_name": "5-FU", "default_route": "IV"}, headers=H(at))
    fu = r.get_json()["data"]
    check("admin creates a drug", r.status_code == 201 and fu["is_active"])
    check("duplicate drug name (case-insensitive) → 409", c.post("/api/v1/chemotherapy/drugs", json={"generic_name": "cisplatin"}, headers=H(at)).status_code == 409)
    check("bad route → 400", "default_route" in err(c.post("/api/v1/chemotherapy/drugs", json={"generic_name": "Y", "default_route": "IM"}, headers=H(at)))[2])
    r = c.post("/api/v1/chemotherapy/regimens", json={"name": "PF q3w", "cycle_length_days": 21, "default_total_cycles": 3, "emetogenic_risk": "high",
                                                      "drugs": [{"drug_id": cis["id"], "dose_value": 100, "dose_unit": "mg/m2", "route": "IV", "day_of_cycle": "1"},
                                                                {"drug_id": fu["id"], "dose_value": 1000, "dose_unit": "mg/m2", "route": "IV", "day_of_cycle": "1-4"}]}, headers=H(at))
    pf = r.get_json()["data"]
    check("admin creates a regimen with drugs", r.status_code == 201 and [d["drug"]["generic_name"] for d in pf["drugs"]] == ["Cisplatin", "Fluorouracil"])
    check("regimen with an unknown drug → 400", "drugs[0].drug_id" in err(c.post("/api/v1/chemotherapy/regimens", json={
        "name": "Bad", "cycle_length_days": 21, "drugs": [{"drug_id": 9999}]}, headers=H(at)))[2])
    check("nurse reads regimens, cannot write", c.get("/api/v1/chemotherapy/regimens", headers=H(nt)).status_code == 200
          and c.patch(f"/api/v1/chemotherapy/regimens/{pf['id']}", json={"description": "x"}, headers=H(nt)).status_code == 403)
    c.patch(f"/api/v1/chemotherapy/drugs/{fu['id']}", json={"is_active": False}, headers=H(at))
    check("deactivated drug hidden from the list (include_inactive shows it)",
          all(d["id"] != fu["id"] for d in c.get("/api/v1/chemotherapy/drugs", headers=H(nt)).get_json()["data"])
          and any(d["id"] == fu["id"] for d in c.get("/api/v1/chemotherapy/drugs?include_inactive=true", headers=H(nt)).get_json()["data"]))
    c.patch(f"/api/v1/chemotherapy/drugs/{fu['id']}", json={"is_active": True}, headers=H(at))

    # ================================================================ new patient → plan → cycles
    newp = c.post("/api/v1/patients", json={"display_name": "化療測試 甲", "gender": "female", "date_of_birth": "1970-01-01",
                                            "account": {"email": "chemo.p@demo.local"}}, headers=H(at)).get_json()["data"]
    PID, tmp = newp["id"], newp["account"]["temporary_password"]
    c.post(f"/api/v1/patients/{PID}/nurse-assignments", json={"nurse_id": nurse.public_id, "is_primary": True}, headers=H(at))
    dx = c.post(f"/api/v1/patients/{PID}/diagnoses", json={"cancer_type_code": "C11", "diagnosis_date": "2026-08-01", "stage": "III"}, headers=H(nt)).get_json()["data"]
    newpt = token("chemo.p@demo.local", tmp)
    c.put("/api/v1/auth/password", json={"current_password": tmp, "new_password": "Chemo2026ok"}, headers=H(newpt))
    newpt = token("chemo.p@demo.local", "Chemo2026ok")
    patient = db.session.query(PatientProfile).filter_by(public_id=PID).one()
    today = datetime.now(timezone.utc) + timedelta(hours=8)  # Asia/Taipei
    today_s = today.date().isoformat()

    plan_body = {"patient_id": PID, "diagnosis_id": dx["id"], "regimen_id": pf["id"], "plan_name": "PF 誘導化療", "intent": "neoadjuvant",
                 "line_of_therapy": 1, "total_cycles": 3, "start_date": today_s, "attending_physician_name": "測試醫師 陳"}
    check("admin cannot create plans (nurse only) → 403", c.post("/api/v1/chemotherapy/plans", json=plan_body, headers=H(at)).status_code == 403)
    check("plan with another patient's diagnosis → 400", "diagnosis_id" in err(c.post("/api/v1/chemotherapy/plans", json={**plan_body, "diagnosis_id": 1}, headers=H(nt)))[2])
    check("plan validation (total_cycles, intent, start_date)", set(err(c.post("/api/v1/chemotherapy/plans", json={
        **plan_body, "total_cycles": 0, "intent": "x", "start_date": "2026-13-01"}, headers=H(nt)))[2]) >= {"total_cycles", "intent", "start_date"})
    r = c.post("/api/v1/chemotherapy/plans", json=plan_body, headers=H(nt))
    plan = r.get_json()["data"]
    check("nurse creates a plan → planned, 3 cycles scheduled every 21 days", r.status_code == 201 and plan["status"] == "planned"
          and [x["scheduled_date"] for x in plan["cycles"]] == [(today.date() + timedelta(days=21 * i)).isoformat() for i in range(3)]
          and all(x["status"] == "scheduled" for x in plan["cycles"]), plan.get("cycles"))
    check("second open plan for the same patient → 409", c.post("/api/v1/chemotherapy/plans", json=plan_body, headers=H(nt)).status_code == 409)
    C1, C2, C3 = (x["id"] for x in plan["cycles"])
    check("dashboard shows the planned plan, no current cycle yet",
          (tp := c.get(f"/api/v1/dashboard/patient/{PID}", headers=H(nt)).get_json()["data"]["widgets"]["treatment-progress"]) is not None
          and tp["current_cycle"] is None, tp)

    # medication before start → 409
    med = {"drug_id": cis["id"], "medication_type": "chemo", "dose_value": 170, "dose_unit": "mg", "route": "IV",
           "administered_at": iso(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)), "infusion_duration_min": 120,
           "administration_status": "given"}
    check("medication for a cycle not started → 409 CYCLE_NOT_STARTED", err(c.post(f"/api/v1/chemotherapy/cycles/{C1}/medications", json=med, headers=H(nt)))[:2] == (409, "CYCLE_NOT_STARTED"))

    # records before the cycle starts: no cycle
    c.post("/api/v1/symptoms/records", json=symptom(), headers=H(newpt, str(uuid.uuid4())))
    pre = db.session.query(SymptomRecord).filter_by(patient_id=patient.id).one()
    check("symptom before the cycle starts: no cycle_id / cycle_day", pre.cycle_id is None and pre.cycle_day is None)

    check("cannot start cycle 2 before cycle 1 → 409", err(c.post(f"/api/v1/chemotherapy/cycles/{C2}/start", json={}, headers=H(nt)))[:2] == (409, "INVALID_STATE"))
    check("start date in the future → 400", "start_date" in err(c.post(f"/api/v1/chemotherapy/cycles/{C1}/start", json={
        "start_date": (today.date() + timedelta(days=1)).isoformat()}, headers=H(nt)))[2])
    r = c.post(f"/api/v1/chemotherapy/cycles/{C1}/start", json={"nadir_start_day": 7, "nadir_end_day": 14, "weight_kg": 58.2, "bsa_m2": 1.6}, headers=H(nt))
    cy = r.get_json()["data"]
    check("start cycle 1 → in_progress, Day 1 today, plan active", r.status_code == 200 and cy["status"] == "in_progress"
          and cy["actual_start_date"] == today_s and cy["cycle_day"] == 1
          and c.get(f"/api/v1/chemotherapy/plans/{plan['id']}", headers=H(nt)).get_json()["data"]["status"] == "active")
    db.session.expire_all()
    check("existing record NOT recalculated when the cycle starts", db.session.get(SymptomRecord, pre.id).cycle_id is None
          and db.session.get(SymptomRecord, pre.id).cycle_day is None)
    check("starting twice → 409", c.post(f"/api/v1/chemotherapy/cycles/{C1}/start", json={}, headers=H(nt)).status_code == 409)

    # ---------------------------------------------------------------- medication + idempotency
    key = str(uuid.uuid4())
    r1 = c.post(f"/api/v1/chemotherapy/cycles/{C1}/medications", json=med, headers=H(nt, key))
    r2 = c.post(f"/api/v1/chemotherapy/cycles/{C1}/medications", json=med, headers=H(nt, key))
    m1 = r1.get_json()["data"]
    check("medication recorded: cycle_day 1, final, by the nurse", r1.status_code == 201 and m1["cycle_day"] == 1 and m1["record_status"] == "final"
          and m1["administered_by"]["id"] == nurse.public_id and m1["dose_value"] == 170.0, m1)
    check("same Idempotency-Key → replayed, no second row", r2.status_code == 201 and r2.headers.get("Idempotent-Replayed") == "true"
          and r2.get_json()["data"]["id"] == m1["id"]
          and db.session.query(MedicationRecord).filter_by(patient_id=patient.id).count() == 1)
    check("same key, different body → 422", c.post(f"/api/v1/chemotherapy/cycles/{C1}/medications", json={**med, "dose_value": 1}, headers=H(nt, key)).status_code == 422)
    check("medication validation (dose, status, time outside the cycle)", set(err(c.post(f"/api/v1/chemotherapy/cycles/{C1}/medications", json={
        **med, "dose_value": -1, "administration_status": "x", "administered_at": "2020-01-01T00:00:00Z"}, headers=H(nt)))[2])
          >= {"dose_value", "administration_status", "administered_at"})
    check("future administration time → 400", "administered_at" in err(c.post(f"/api/v1/chemotherapy/cycles/{C1}/medications", json={
        **med, "administered_at": iso(datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2))}, headers=H(nt)))[2])

    # ---------------------------------------------------------------- dashboard / timeline / risk
    dash = c.get("/api/v1/dashboard/patient/me", headers=H(newpt)).get_json()["data"]["widgets"]
    check("patient dashboard: current cycle 1 day 1, 3 cycles", dash["treatment-progress"]["current_cycle"]["cycle_number"] == 1
          and dash["treatment-progress"]["current_cycle"]["cycle_day"] == 1, dash["treatment-progress"])
    tl = c.get("/api/v1/patients/me/timeline", headers=H(newpt)).get_json()["data"]
    chemo = [e for e in tl if e["event_type"] == "CHEMOTHERAPY"]
    check("timeline: cycle start + medication appear", {e["event_id"].split(":")[1] for e in chemo} >= {"cycle_start", "medication"}, [e["event_id"] for e in chemo])
    med_ev = next(e for e in chemo if e["event_id"] == f"CHEMOTHERAPY:medication:{m1['id']}")
    check("patient timeline hides administered_by / reaction notes", "administered_by" not in med_ev["detail"] and "reaction_notes" not in med_ev["detail"])
    r = c.post("/api/v1/symptoms/records", json=symptom(pain=9), headers=H(newpt, str(uuid.uuid4())))
    rec = r.get_json()["data"]
    check("symptom after start: cycle_id = cycle 1, cycle_day 1", rec["cycle_day"] == 1
          and db.session.get(SymptomRecord, rec["id"]).cycle_id == C1)
    check("risk engine unchanged: severe pain alert → nurse notification", any(a["alert_rule_code"] == "severe_pain" for a in rec["triggered_alerts"])
          and any(n["alert_rule"]["code"] == "severe_pain" for n in c.get(f"/api/v1/notifications?patient_id={PID}", headers=H(nt)).get_json()["data"]))
    cl = next(x for x in c.get("/api/v1/dashboard/widgets/caseload/data", headers=H(nt)).get_json()["data"] if x["patient_id"] == PID)
    check("caseload shows the cycle", cl["cycle"] == {"cycle_number": 1, "cycle_day": 1}, cl["cycle"])

    # ---------------------------------------------------------------- correction / mark error
    check("amend without a reason → 400", "amend_reason" in err(c.post(f"/api/v1/chemotherapy/medications/{m1['id']}/amend", json={"dose_value": 160}, headers=H(nt)))[2])
    check("amend with nothing changed → 400", err(c.post(f"/api/v1/chemotherapy/medications/{m1['id']}/amend", json={"amend_reason": "x"}, headers=H(nt)))[0] == 400)
    akey = str(uuid.uuid4())
    r = c.post(f"/api/v1/chemotherapy/medications/{m1['id']}/amend", json={"amend_reason": "劑量誤植", "dose_value": 160}, headers=H(nt, akey))
    m2 = r.get_json()["data"]
    check("amend → new final record amends the original", r.status_code == 201 and m2["amends_id"] == m1["id"] and m2["dose_value"] == 160.0
          and m2["drug"]["id"] == cis["id"] and m2["administered_at"] == m1["administered_at"] and m2["cycle_day"] == 1)
    check("amend replay (same key) does not create a third record", c.post(f"/api/v1/chemotherapy/medications/{m1['id']}/amend",
          json={"amend_reason": "劑量誤植", "dose_value": 160}, headers=H(nt, akey)).get_json()["data"]["id"] == m2["id"]
          and db.session.query(MedicationRecord).filter_by(patient_id=patient.id).count() == 2)
    hist = c.get(f"/api/v1/chemotherapy/cycles/{C1}/medications", headers=H(nt)).get_json()["data"]
    orig = next(x for x in hist if x["id"] == m1["id"])
    check("history keeps the original (amended, points to the correction)", orig["record_status"] == "amended" and orig["amended_by_id"] == m2["id"]
          and orig["dose_value"] == 170.0)
    check("amending an amended record → 409", c.post(f"/api/v1/chemotherapy/medications/{m1['id']}/amend", json={"amend_reason": "x", "dose_value": 1}, headers=H(nt)).status_code == 409)
    pmeds = c.get("/api/v1/chemotherapy/medications?patient_id=me", headers=H(newpt)).get_json()["data"]
    check("patient sees only the corrected record, without internals", [x["id"] for x in pmeds] == [m2["id"]]
          and not {"administered_by", "reaction_notes", "amends_id"} & set(pmeds[0]))
    tl = c.get("/api/v1/patients/me/timeline", headers=H(newpt)).get_json()["data"]
    meds_tl = [e["source_id"] for e in tl if e["event_id"].startswith("CHEMOTHERAPY:medication:")]
    check("timeline shows the corrected dose only", meds_tl == [m2["id"]] and "160 mg" in next(e["summary"] for e in tl if e["source_id"] == m2["id"] and e["event_type"] == "CHEMOTHERAPY"))
    r = c.post(f"/api/v1/chemotherapy/cycles/{C1}/medications", json={**med, "medication_type": "premedication", "drug_id": fu["id"], "dose_value": 8}, headers=H(nt))
    m3 = r.get_json()["data"]
    check("mark-error needs a reason", "reason" in err(c.post(f"/api/v1/chemotherapy/medications/{m3['id']}/mark-error", json={}, headers=H(nt)))[2])
    r = c.post(f"/api/v1/chemotherapy/medications/{m3['id']}/mark-error", json={"reason": "登錄到錯的病人"}, headers=H(nt))
    check("mark-error → entered_in_error, hidden from patient and timeline", r.get_json()["data"]["record_status"] == "entered_in_error"
          and all(x["id"] != m3["id"] for x in c.get("/api/v1/chemotherapy/medications?patient_id=me", headers=H(newpt)).get_json()["data"])
          and all(e["source_id"] != m3["id"] for e in c.get("/api/v1/patients/me/timeline", headers=H(newpt)).get_json()["data"] if e["event_type"] == "CHEMOTHERAPY"))
    check("cycle medication_count counts final records only", c.get(f"/api/v1/chemotherapy/cycles/{C1}", headers=H(nt)).get_json()["data"]["medication_count"] == 1)

    # ---------------------------------------------------------------- permissions / privacy
    other_nurse = User(role=nurse.role, email="n2@demo.local", password_hash=nurse.password_hash, display_name="其他護理師",
                       password_changed_at=nurse.password_changed_at)
    db.session.add(other_nurse)
    db.session.commit()
    ot = token("n2@demo.local")
    codes = [c.get(u, headers=H(ot)).status_code for u in (f"/api/v1/chemotherapy/plans?patient_id={PID}", f"/api/v1/chemotherapy/plans/{plan['id']}",
                                                          f"/api/v1/chemotherapy/cycles/{C1}", f"/api/v1/chemotherapy/cycles/{C1}/medications",
                                                          f"/api/v1/chemotherapy/medications?patient_id={PID}")]
    check("unassigned nurse: every read → 404", codes == [404] * 5, codes)
    check("unassigned nurse: writes → 404", [c.post(u, json=b, headers=H(ot)).status_code for u, b in (
        (f"/api/v1/chemotherapy/cycles/{C1}/medications", med), (f"/api/v1/chemotherapy/medications/{m2['id']}/amend", {"amend_reason": "x", "dose_value": 1}),
        (f"/api/v1/chemotherapy/cycles/{C2}/delay", {"new_scheduled_date": "2030-01-01", "delay_reason": "x"}))] == [404] * 3)
    check("other patient: plans of P00001 → 404", c.get(f"/api/v1/chemotherapy/plans?patient_id={p1.public_id}", headers=H(newpt)).status_code == 404
          and c.get(f"/api/v1/chemotherapy/cycles/{C1}", headers=H(pt)).status_code == 404)
    check("patient cannot write (403)", c.post(f"/api/v1/chemotherapy/cycles/{C1}/medications", json=med, headers=H(newpt)).status_code == 403
          and c.post(f"/api/v1/chemotherapy/cycles/{C1}/complete", json={}, headers=H(newpt)).status_code == 403)
    pplan = c.get("/api/v1/chemotherapy/plans?patient_id=me", headers=H(newpt)).get_json()["data"][0]
    check("patient reads own plan without staff-only fields", pplan["id"] == plan["id"] and "created_by" not in pplan
          and not {"weight_kg", "bsa_m2", "notes"} & set(pplan["cycles"][0]))
    check("admin reads every plan", c.get(f"/api/v1/chemotherapy/plans/{plan['id']}", headers=H(at)).status_code == 200)
    check("unknown ids → 404", c.get("/api/v1/chemotherapy/plans/99999", headers=H(nt)).status_code == 404
          and c.get("/api/v1/chemotherapy/cycles/99999", headers=H(nt)).status_code == 404
          and c.post("/api/v1/chemotherapy/medications/99999/amend", json={}, headers=H(nt)).status_code == 404)

    # ---------------------------------------------------------------- complete / next cycle / delay
    check("cycle PATCH cannot change the start date", "actual_start_date" in err(c.patch(f"/api/v1/chemotherapy/cycles/{C1}", json={"actual_start_date": "2026-01-01"}, headers=H(nt)))[2])
    check("delay a started cycle → 409", c.post(f"/api/v1/chemotherapy/cycles/{C1}/delay", json={"new_scheduled_date": "2030-01-01", "delay_reason": "x"}, headers=H(nt)).status_code == 409)
    r = c.post(f"/api/v1/chemotherapy/cycles/{C2}/delay", json={"new_scheduled_date": (today.date() + timedelta(days=24)).isoformat(), "delay_reason": "ANC 過低"}, headers=H(nt))
    check("delay cycle 2 by 3 days → delayed, delay_days 3", r.get_json()["data"]["status"] == "delayed" and r.get_json()["data"]["delay_days"] == 3)
    check("delay to an earlier date → 400", "new_scheduled_date" in err(c.post(f"/api/v1/chemotherapy/cycles/{C2}/delay", json={"new_scheduled_date": today_s, "delay_reason": "x"}, headers=H(nt)))[2])
    before = snapshot(patient.id)
    r = c.post(f"/api/v1/chemotherapy/cycles/{C1}/complete", json={}, headers=H(nt))
    check("complete cycle 1 → completed, end today", r.get_json()["data"]["status"] == "completed" and r.get_json()["data"]["actual_end_date"] == today_s)
    check("start cycle 2 on the same day as cycle 1 → 400 (must be after the previous cycle)",
          "start_date" in err(c.post(f"/api/v1/chemotherapy/cycles/{C2}/start", json={}, headers=H(nt)))[2])
    db.session.expire_all()
    check("records unchanged by completing the cycle", snapshot(patient.id) == before)

    # seed patient: complete cycle 1 (started 3 days ago), add + start cycle 2 today → new records only
    seed_plan = c.get("/api/v1/chemotherapy/plans?patient_id=" + p1.public_id, headers=H(nt)).get_json()["data"][0]
    s1 = seed_plan["cycles"][0]["id"]
    before = snapshot(p1.id)
    old_cycle_days = {r[0]: r[2] for r in before["SymptomRecord"]}
    c.post(f"/api/v1/chemotherapy/cycles/{s1}/complete", json={"end_date": (today.date() - timedelta(days=1)).isoformat()}, headers=H(nt))
    r = c.post(f"/api/v1/chemotherapy/plans/{seed_plan['id']}/cycles", json={"scheduled_date": today_s}, headers=H(nt))
    s2 = r.get_json()["data"]
    check("add cycle 2 to the seed plan", r.status_code == 201 and s2["cycle_number"] == 2)
    r = c.post(f"/api/v1/chemotherapy/cycles/{s2['id']}/start", json={}, headers=H(nt))
    check("start cycle 2: nadir window carried from cycle 1", r.get_json()["data"]["nadir_start_day"] == 7 and r.get_json()["data"]["nadir_end_day"] == 14)
    db.session.expire_all()
    check("seed history keeps cycle 1 ids / days after cycle 2 starts (no recalculation)", snapshot(p1.id) == before)
    r = c.post("/api/v1/symptoms/records", json=symptom(), headers=H(pt, str(uuid.uuid4())))
    new_rec = db.session.get(SymptomRecord, r.get_json()["data"]["id"])
    check("new record after cycle 2 starts → cycle 2, day 1", new_rec.cycle_id == s2["id"] and new_rec.cycle_day == 1
          and all(db.session.get(SymptomRecord, i).cycle_day == d for i, d in old_cycle_days.items()))
    check("dashboard now shows cycle 2 day 1", c.get("/api/v1/dashboard/patient/me", headers=H(pt)).get_json()["data"]["widgets"]["treatment-progress"]["current_cycle"]["cycle_number"] == 2)
    check("a second running cycle for the same patient is impossible", c.post(f"/api/v1/chemotherapy/plans/{seed_plan['id']}/cycles", json={"scheduled_date": today_s}, headers=H(nt)).status_code == 201
          and err(c.post(f"/api/v1/chemotherapy/cycles/{s2['id'] + 1}/start", json={}, headers=H(nt)))[:2] == (409, "INVALID_STATE"))
    check("add beyond total_cycles → 409", c.post(f"/api/v1/chemotherapy/plans/{seed_plan['id']}/cycles", json={"scheduled_date": today_s}, headers=H(nt)).status_code == 409)
    check("total_cycles below existing cycles → 400", "total_cycles" in err(c.patch(f"/api/v1/chemotherapy/plans/{seed_plan['id']}", json={"total_cycles": 2}, headers=H(nt)))[2])

    # discontinue the new patient's plan
    check("discontinue needs a reason", "discontinue_reason" in err(c.post(f"/api/v1/chemotherapy/plans/{plan['id']}/discontinue", json={}, headers=H(nt)))[2])
    r = c.post(f"/api/v1/chemotherapy/plans/{plan['id']}/discontinue", json={"discontinue_reason": "病人決定轉院"}, headers=H(nt))
    d = r.get_json()["data"]
    check("discontinue → plan discontinued, remaining cycles cancelled", d["status"] == "discontinued"
          and [x["status"] for x in d["cycles"]] == ["completed", "cancelled", "cancelled"])
    check("closed plan cannot be edited → 409", c.patch(f"/api/v1/chemotherapy/plans/{plan['id']}", json={"plan_name": "x"}, headers=H(nt)).status_code == 409)
    check("dashboard: no active plan after discontinuing", c.get("/api/v1/dashboard/patient/me", headers=H(newpt)).get_json()["data"]["widgets"]["treatment-progress"] is None)
    check("a new plan can now be created", c.post("/api/v1/chemotherapy/plans", json={**plan_body, "generate_cycles": False, "regimen_id": None}, headers=H(nt)).status_code == 201)

    # ================================================================ audit
    logs = db.session.query(AuditLog).all()
    by = lambda action, rt: [l for l in logs if l.action == action and l.resource_type == rt]  # noqa: E731
    check("audit: plan CREATE / UPDATE (discontinue)", by("CREATE", "chemotherapy_plans") and any(l.changes.get("status") == "discontinued" for l in by("UPDATE", "chemotherapy_plans")))
    check("audit: cycle start / complete / delay transitions", {l.changes.get("transition") for l in by("UPDATE", "chemotherapy_cycles")} >= {"start", "complete", "delay"})
    check("audit: medication CREATE (once for the replayed key)", len([l for l in by("CREATE", "medication_records") if l.resource_id == str(m1["id"])]) == 1)
    am = by("AMEND", "medication_records")
    check("audit: AMEND on the original with reason, new id and fields", len(am) == 1 and am[0].resource_id == str(m1["id"])
          and am[0].changes == {"new_record_id": m2["id"], "amend_reason": "劑量誤植", "fields": ["dose_value"]} and am[0].actor_user_id == nurse.id)
    check("audit: MARK_ERROR with reason", [l.changes for l in by("MARK_ERROR", "medication_records")] == [{"reason": "登錄到錯的病人"}])
    check("audit: drug / regimen master data writes", by("CREATE", "drugs") and by("UPDATE", "drugs") and by("CREATE", "chemo_regimens"))
    check("audit: plan views recorded", any(l.action == "VIEW" and l.resource_type == "chemotherapy_plans" for l in logs))
    check("no password in any audit entry", not any(x in json.dumps([l.changes for l in logs], ensure_ascii=False) for x in (tmp, "Chemo2026ok", "Demo@1234")))

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
