"""Sprint 1: patient & care-team management — patients, codes, accounts, first-login password
change, assignments, access after reassignment, privacy, permissions, audit, full flow."""
import sys
from pathlib import Path
import json
import re
import uuid
import warnings
from datetime import date
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from werkzeug.security import check_password_hash

from app import create_app
from app.extensions import db
from app.models import (AuditLog, LabResult, MedicationRecord, Notification, NursePatientAssignment, PatientProfile,
                        SymptomRecord, User, VitalSign)
from app.models.base import utcnow
from app.modules.patient import management
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()
TEMP_RE = re.compile(r"^[A-Za-z2-9]{4}-[A-Za-z2-9]{4}-[A-Za-z2-9]{4}$")


def login(email, password="Demo@1234"):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    return r


def token(email, password="Demo@1234"):
    return login(email, password).get_json()["data"]["access_token"]


def H(t, key=None):
    return {"Authorization": f"Bearer {t}", **({"Idempotency-Key": key} if key else {})}


def err(r):
    j = r.get_json() or {}
    return r.status_code, j.get("error", {}).get("code"), [d.get("field") for d in j.get("error", {}).get("details", [])]


NEW = {"display_name": "測試病人 丙", "gender": "female", "date_of_birth": "1972-03-04", "height_cm": 160.5,
       "blood_type": "A", "allergies": "無", "baseline_ecog": 1, "timezone": "Asia/Taipei"}

with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    at, nt, pt = token("admin01@demo.local"), token("nurse01@demo.local"), token("patient01@demo.local")
    secrets_seen = []  # every temporary password issued — must never appear in audit logs

    # ================================================================ create / code generation
    r = c.post("/api/v1/patients", json=NEW, headers=H(nt))
    d = r.get_json()["data"]
    check("nurse creates a patient → 201 with the next code P00002 (seed has P00001)", r.status_code == 201 and d["patient_code"] == "P00002"
          and d["is_demo"] is True and d["account"] is None, d)
    P2 = d["id"]
    r = c.post("/api/v1/patients", json={**NEW, "display_name": "測試病人 丁", "account": {"email": "Patient.Ding@Demo.Local"}}, headers=H(at))
    d = r.get_json()["data"]
    check("admin creates a patient with an account → P00003, temporary password returned once",
          r.status_code == 201 and d["patient_code"] == "P00003" and TEMP_RE.match(d["account"]["temporary_password"] or "")
          and d["account"]["email"] == "patient.ding@demo.local" and d["account"]["must_change_password"] is True, d.get("account"))
    P3, TEMP3 = d["id"], d["account"]["temporary_password"]
    secrets_seen.append(TEMP3)
    check("codes are sequential with no gaps", [p.patient_code for p in db.session.query(PatientProfile).order_by(PatientProfile.id)] == ["P00001", "P00002", "P00003"])
    p3 = db.session.query(PatientProfile).filter_by(public_id=P3).one()
    check("temporary password stored only as a werkzeug hash", check_password_hash(p3.user.password_hash, TEMP3) and TEMP3 not in p3.user.password_hash)

    # duplicate protection: the first generated code collides with a code taken concurrently
    real_next = management.next_patient_code
    calls = []

    def racing_next(taken=()):
        calls.append(tuple(taken))
        return "P00003" if not taken else real_next(taken)  # stale: P00003 already exists
    with patch.object(management, "next_patient_code", racing_next):
        r = c.post("/api/v1/patients", json={**NEW, "display_name": "撞號測試"}, headers=H(nt))
    check("code collision → retried with the next free code (P00004), no duplicate", r.status_code == 201
          and r.get_json()["data"]["patient_code"] == "P00004" and len(calls) == 2, (r.get_json()["data"], calls))
    check("UNIQUE(patient_code) still enforced in the database",
          db.session.query(PatientProfile).filter_by(patient_code="P00003").count() == 1)
    P4 = r.get_json()["data"]["id"]
    # soft-deleted codes are never reused
    p4 = db.session.query(PatientProfile).filter_by(public_id=P4).one()
    p4.deleted_at = utcnow()
    db.session.commit()
    r = c.post("/api/v1/patients", json={**NEW, "display_name": "測試病人 戊"}, headers=H(nt))
    check("soft-deleted P00004 is not reused → P00005", r.get_json()["data"]["patient_code"] == "P00005")
    P5 = r.get_json()["data"]["id"]

    # validation
    check("national_id / id_number rejected (no identity numbers stored)",
          set(err(c.post("/api/v1/patients", json={**NEW, "national_id": "A123456789", "id_number": "x"}, headers=H(nt)))[2]) >= {"national_id", "id_number"})
    check("patient_code cannot be supplied", "patient_code" in err(c.post("/api/v1/patients", json={**NEW, "patient_code": "P99999"}, headers=H(nt)))[2])
    check("required fields / bad values → 400 with fields",
          set(err(c.post("/api/v1/patients", json={"gender": "x", "date_of_birth": "2999-01-01", "timezone": "Mars/Base", "baseline_ecog": 9}, headers=H(nt)))[2])
          >= {"display_name", "gender", "date_of_birth", "timezone", "baseline_ecog"})
    check("duplicate account email → 409", err(c.post("/api/v1/patients", json={**NEW, "account": {"email": "nurse01@demo.local"}}, headers=H(nt)))[:2] == (409, "CONFLICT"))
    check("failed creation consumes no code", db.session.query(PatientProfile).count() == 5)

    # ================================================================ permissions (roles)
    check("patient cannot list / create / update patients → 403",
          c.get("/api/v1/patients", headers=H(pt)).status_code == 403 and c.post("/api/v1/patients", json=NEW, headers=H(pt)).status_code == 403
          and c.patch("/api/v1/patients/me", json={"allergies": "x"}, headers=H(pt)).status_code == 403)
    check("nurse cannot create nurses or assignments → 403",
          c.post("/api/v1/admin/users", json={"email": "x@demo.local", "display_name": "x"}, headers=H(nt)).status_code == 403
          and c.post(f"/api/v1/patients/{P3}/nurse-assignments", json={"nurse_id": nurse.public_id}, headers=H(nt)).status_code == 403)
    check("patient cannot create nurses or assignments → 403",
          c.post("/api/v1/admin/users", json={"email": "x@demo.local", "display_name": "x"}, headers=H(pt)).status_code == 403
          and c.post(f"/api/v1/patients/{P3}/nurse-assignments", json={"nurse_id": nurse.public_id}, headers=H(pt)).status_code == 403)

    # ================================================================ list / detail scope
    nl = c.get("/api/v1/patients", headers=H(nt)).get_json()
    check("nurse list = currently assigned patients only (P00001; new patients unassigned)", [i["patient_code"] for i in nl["data"]] == ["P00001"]
          and nl["data"][0]["is_primary_nurse"] is True, [i["patient_code"] for i in nl["data"]])
    al = c.get("/api/v1/patients", headers=H(at)).get_json()
    check("admin list = all non-deleted patients", [i["patient_code"] for i in al["data"]] == ["P00001", "P00002", "P00003", "P00005"] and al["meta"]["total"] == 4)
    check("admin filter assigned=false → the unassigned ones",
          [i["patient_code"] for i in c.get("/api/v1/patients?assigned=false", headers=H(at)).get_json()["data"]] == ["P00002", "P00003", "P00005"])
    check("search q by code or name", [i["patient_code"] for i in c.get("/api/v1/patients?q=丁", headers=H(at)).get_json()["data"]] == ["P00003"]
          and [i["patient_code"] for i in c.get("/api/v1/patients?q=P00005", headers=H(at)).get_json()["data"]] == ["P00005"])
    check("nurse opening an unassigned patient (even one they created) → 404", c.get(f"/api/v1/patients/{P2}", headers=H(nt)).status_code == 404)
    check("unknown / malformed / deleted patient id → 404",
          c.get(f"/api/v1/patients/{uuid.uuid4()}", headers=H(at)).status_code == 404 and c.get("/api/v1/patients/not-a-uuid", headers=H(at)).status_code == 404
          and c.get(f"/api/v1/patients/{P4}", headers=H(at)).status_code == 404)
    det = c.get(f"/api/v1/patients/{p1.public_id}", headers=H(nt)).get_json()["data"]
    check("staff detail: profile, care alerts, diagnoses, care team, account state",
          det["patient_code"] == "P00001" and det["care_alerts"][0]["description"] == "Penicillin 過敏" and det["diagnoses"][0]["cancer_type"]["code"] == "C11"
          and det["care_team"]["primary_nurse"]["display_name"] == "測試護理師 林" and det["account"]["has_account"] is True)

    # ================================================================ update
    r = c.patch(f"/api/v1/patients/{p1.public_id}", json={"allergies": "Penicillin、海鮮", "height_cm": 171}, headers=H(nt))
    check("assigned nurse updates the profile", r.status_code == 200 and r.get_json()["data"]["allergies"] == "Penicillin、海鮮")
    check("patient_code is immutable", "patient_code" in err(c.patch(f"/api/v1/patients/{p1.public_id}", json={"patient_code": "P00009"}, headers=H(nt)))[2])
    check("admin updates any patient", c.patch(f"/api/v1/patients/{P2}", json={"display_name": "測試病人 丙（改）"}, headers=H(at)).status_code == 200)
    check("nurse cannot update an unassigned patient → 404", c.patch(f"/api/v1/patients/{P2}", json={"allergies": "x"}, headers=H(nt)).status_code == 404)

    # ================================================================ care alerts / diagnoses / cancer types
    check("cancer types list", any(t["code"] == "C11" for t in c.get("/api/v1/patients/cancer-types", headers=H(pt)).get_json()["data"]))
    r = c.post(f"/api/v1/patients/{p1.public_id}/care-alerts", json={"alert_type": "limb_restriction", "body_site": "right_arm",
                                                                    "description": "右手禁止量血壓", "severity": "high"}, headers=H(nt))
    alert_id = r.get_json()["data"]["id"]
    check("nurse adds a care alert", r.status_code == 201)
    check("limb restriction needs a body site", "body_site" in err(c.post(f"/api/v1/patients/{p1.public_id}/care-alerts",
                                                                          json={"alert_type": "limb_restriction", "description": "x", "severity": "high"}, headers=H(nt)))[2])
    c.patch(f"/api/v1/patients/{p1.public_id}/care-alerts/{alert_id}", json={"is_active": False}, headers=H(nt))
    check("deactivated care alert hidden from the active list, kept with include_inactive",
          all(a["id"] != alert_id for a in c.get(f"/api/v1/patients/{p1.public_id}/care-alerts", headers=H(nt)).get_json()["data"])
          and any(a["id"] == alert_id for a in c.get(f"/api/v1/patients/{p1.public_id}/care-alerts?include_inactive=true", headers=H(nt)).get_json()["data"]))
    check("admin cannot write care alerts (nurse only, api-design §4)", c.post(f"/api/v1/patients/{p1.public_id}/care-alerts",
                                                                              json={"alert_type": "allergy", "description": "x", "severity": "low"}, headers=H(at)).status_code == 403)
    r = c.post(f"/api/v1/patients/{p1.public_id}/diagnoses", json={"cancer_type_code": "C11", "diagnosis_date": "2026-01-02", "stage": "IV", "is_primary": True}, headers=H(nt))
    new_dx = r.get_json()["data"]
    dxs = c.get(f"/api/v1/patients/{p1.public_id}/diagnoses", headers=H(nt)).get_json()["data"]
    check("new primary diagnosis demotes the previous primary", r.status_code == 201 and [d["is_primary"] for d in dxs].count(True) == 1
          and next(d for d in dxs if d["id"] == new_dx["id"])["is_primary"])
    check("unknown cancer type → 400", "cancer_type_code" in err(c.post(f"/api/v1/patients/{p1.public_id}/diagnoses",
                                                                       json={"cancer_type_code": "XX", "diagnosis_date": "2026-01-01"}, headers=H(nt)))[2])
    check("diagnosis update", c.patch(f"/api/v1/patients/{p1.public_id}/diagnoses/{new_dx['id']}", json={"status": "remission"}, headers=H(nt)).get_json()["data"]["status"] == "remission")

    # ================================================================ nurse accounts (admin)
    r = c.post("/api/v1/admin/users", json={"email": "nurse.chen@demo.local", "display_name": "測試護理師 陳", "role": "nurse",
                                            "nurse_profile": {"staff_code": "N0002", "department": "日間化療室"}}, headers=H(at))
    d = r.get_json()["data"]
    check("admin creates a nurse → temporary password once, must change", r.status_code == 201 and TEMP_RE.match(d["temporary_password"])
          and d["must_change_password"] is True and d["nurse_profile"]["staff_code"] == "N0002")
    NURSE2, NURSE2_TEMP = d["id"], d["temporary_password"]
    secrets_seen.append(NURSE2_TEMP)
    check("admin cannot choose the password; patients are not created here", set(err(c.post("/api/v1/admin/users", json={
        "email": "a@demo.local", "display_name": "a", "role": "patient", "temporary_password": "x"}, headers=H(at)))[2]) >= {"role", "temporary_password"})
    check("duplicate staff code → 409", err(c.post("/api/v1/admin/users", json={"email": "z@demo.local", "display_name": "z",
                                                                                 "nurse_profile": {"staff_code": "N0002"}}, headers=H(at)))[:2] == (409, "CONFLICT"))
    staff = c.get("/api/v1/admin/users?role=nurse", headers=H(at)).get_json()["data"]
    check("admin lists nurses with their active patient counts", {s["email"]: s["active_patient_count"] for s in staff}
          == {"nurse01@demo.local": 1, "nurse.chen@demo.local": 0} and all("temporary_password" not in s for s in staff))

    # ================================================================ first-login password change
    r = login("nurse.chen@demo.local", NURSE2_TEMP)
    check("new nurse logs in with the temporary password; must_change_password = true", r.status_code == 200
          and r.get_json()["data"]["user"]["must_change_password"] is True)
    t2tmp = r.get_json()["data"]["access_token"]
    e = err(c.get("/api/v1/dashboard/widgets/caseload/data", headers=H(t2tmp)))
    check("before changing it, other APIs → 403 PASSWORD_CHANGE_REQUIRED", e[:2] == (403, "PASSWORD_CHANGE_REQUIRED")
          and err(c.get("/api/v1/patients", headers=H(t2tmp)))[1] == "PASSWORD_CHANGE_REQUIRED")
    check("/auth/me still works (the app needs it to show the change form)", c.get("/api/v1/auth/me", headers=H(t2tmp)).get_json()["data"]["must_change_password"] is True)
    check("wrong current password → 400 current_password", "current_password" in err(c.put("/api/v1/auth/password", json={"current_password": "nope", "new_password": "NewPass123"}, headers=H(t2tmp)))[2])
    check("weak new password (no digit / too short) → 400 new_password", err(c.put("/api/v1/auth/password", json={"current_password": NURSE2_TEMP, "new_password": "abcdefgh"}, headers=H(t2tmp)))[2] == ["new_password"]
          and err(c.put("/api/v1/auth/password", json={"current_password": NURSE2_TEMP, "new_password": "a1"}, headers=H(t2tmp)))[2] == ["new_password"])
    check("same as the temporary password → 400", err(c.put("/api/v1/auth/password", json={"current_password": NURSE2_TEMP, "new_password": NURSE2_TEMP}, headers=H(t2tmp)))[2] == ["new_password"])
    r = c.put("/api/v1/auth/password", json={"current_password": NURSE2_TEMP, "new_password": "ChenNurse2026"}, headers=H(t2tmp))
    check("valid change → 204, then the same token can use the API", r.status_code == 204
          and c.get("/api/v1/dashboard/widgets/caseload/data", headers=H(t2tmp)).status_code == 200)
    check("temporary password no longer works; new one does", login("nurse.chen@demo.local", NURSE2_TEMP).status_code == 401
          and login("nurse.chen@demo.local", "ChenNurse2026").get_json()["data"]["user"]["must_change_password"] is False)
    t2 = token("nurse.chen@demo.local", "ChenNurse2026")

    # ================================================================ assignments
    r = c.post(f"/api/v1/patients/{P3}/nurse-assignments", json={"nurse_id": nurse.public_id, "is_primary": True}, headers=H(at))
    A1 = r.get_json()["data"]["id"]
    check("admin assigns nurse01 to P00003 (primary)", r.status_code == 201 and r.get_json()["data"]["nurse"]["id"] == nurse.public_id)
    check("the assigned nurse can see the patient immediately", c.get(f"/api/v1/patients/{P3}", headers=H(nt)).status_code == 200
          and "P00003" in [i["patient_code"] for i in c.get("/api/v1/patients", headers=H(nt)).get_json()["data"]]
          and "P00003" in [i["patient_code"] for i in c.get("/api/v1/dashboard/widgets/caseload/data", headers=H(nt)).get_json()["data"]])
    check("duplicate active assignment → 409", err(c.post(f"/api/v1/patients/{P3}/nurse-assignments", json={"nurse_id": nurse.public_id}, headers=H(at)))[:2] == (409, "CONFLICT"))
    check("assigning a non-nurse → 400", "nurse_id" in err(c.post(f"/api/v1/patients/{P3}/nurse-assignments", json={"nurse_id": p3.user.public_id}, headers=H(at)))[2])
    check("nurse sees the care team of an assigned patient; not of others",
          c.get(f"/api/v1/patients/{P3}/nurse-assignments", headers=H(nt)).status_code == 200
          and c.get(f"/api/v1/patients/{P5}/nurse-assignments", headers=H(nt)).status_code == 404)

    # ================================================================ full flow: patient first login → symptom report → alert
    r = login("patient.ding@demo.local", TEMP3)
    check("patient first login with the temporary password → must change", r.get_json()["data"]["user"]["must_change_password"] is True)
    ptmp = r.get_json()["data"]["access_token"]
    check("patient blocked until the password is changed", err(c.get("/api/v1/dashboard/patient/me", headers=H(ptmp)))[1] == "PASSWORD_CHANGE_REQUIRED")
    check("patient sets a password → 204", c.put("/api/v1/auth/password", json={"current_password": TEMP3, "new_password": "Ding2026pass"}, headers=H(ptmp)).status_code == 204)
    p3t = token("patient.ding@demo.local", "Ding2026pass")
    check("patient sees their dashboard; other patients 404", c.get("/api/v1/dashboard/patient/me", headers=H(p3t)).status_code == 200
          and c.get(f"/api/v1/patients/{p1.public_id}", headers=H(p3t)).status_code == 404 and c.get(f"/api/v1/dashboard/patient/{p1.public_id}", headers=H(p3t)).status_code == 404)
    me = c.get("/api/v1/patients/me", headers=H(p3t)).get_json()["data"]
    check("patient's own profile: no care team names, no account internals, no creator", me["patient_code"] == "P00003"
          and not {"care_team", "account", "created_by"} & set(me))
    rec = c.post("/api/v1/symptoms/records", json={"patient_id": "me", "form_code": "daily_chemo_check", "values": [
        {"definition_code": "pain", "value_numeric": 9}, {"definition_code": "nausea", "value_numeric": 2},
        {"definition_code": "fatigue", "value_numeric": 3}, {"definition_code": "fever", "value_boolean": False}]}, headers=H(p3t, str(uuid.uuid4())))
    check("patient reports symptoms → 201 with the existing alert (severe pain)", rec.status_code == 201
          and any(a["alert_rule_code"] == "severe_pain" for a in rec.get_json()["data"]["triggered_alerts"]))
    nl = c.get(f"/api/v1/notifications?patient_id={P3}&status=open", headers=H(nt)).get_json()["data"]
    check("assigned nurse receives the alert (existing notification workflow)", [n["alert_rule"]["code"] for n in nl] == ["severe_pain"] and nl[0]["is_mine"])
    alert_nid = nl[0]["id"]
    check("nurse can act on it (acknowledge)", c.post(f"/api/v1/notifications/{alert_nid}/acknowledge", headers=H(nt)).status_code == 200)

    # ================================================================ end assignment → old nurse loses access, new nurse gains it
    rows_before = {m.__name__: sorted((r.id, r.cycle_id, r.cycle_day, str(getattr(r, "record_status"))) for r in db.session.query(m).all())
                   for m in (SymptomRecord, VitalSign, LabResult, MedicationRecord)}
    r = c.post(f"/api/v1/patients/{P3}/nurse-assignments/{A1}/end", headers=H(at))
    check("admin ends the assignment", r.status_code == 200 and r.get_json()["data"]["active"] is False)
    check("ending twice → 409", err(c.post(f"/api/v1/patients/{P3}/nurse-assignments/{A1}/end", headers=H(at)))[:2] == (409, "CONFLICT"))
    urls = [f"/api/v1/patients/{P3}", f"/api/v1/dashboard/patient/{P3}", f"/api/v1/patients/{P3}/timeline", f"/api/v1/labs/results/{P3}",
            f"/api/v1/labs/summary/{P3}", f"/api/v1/symptoms/records/{P3}", f"/api/v1/patients/{P3}/care-alerts",
            f"/api/v1/patients/{P3}/diagnoses", f"/api/v1/patients/{P3}/nurse-assignments", f"/api/v1/notifications/{alert_nid}"]
    codes = [c.get(u, headers=H(nt)).status_code for u in urls]
    check("old nurse: every authorized read of the patient → 404 (same token, immediately)", codes == [404] * len(urls), codes)
    writes = [
        c.patch(f"/api/v1/patients/{P3}", json={"allergies": "x"}, headers=H(nt)).status_code,
        c.post("/api/v1/vital-signs", json={"patient_id": P3, "heart_rate_bpm": 80}, headers=H(nt, str(uuid.uuid4()))).status_code,
        c.post(f"/api/v1/notifications/{alert_nid}/start", headers=H(nt)).status_code,
        c.post(f"/api/v1/symptoms/records/{rec.get_json()['data']['id']}/review", json={"action_note": "x"}, headers=H(nt)).status_code,
    ]
    check("old nurse: writes → 404", writes == [404] * 4, writes)
    check("old nurse: patient gone from list, caseload and notification list",
          "P00003" not in [i["patient_code"] for i in c.get("/api/v1/patients", headers=H(nt)).get_json()["data"]]
          and "P00003" not in [i["patient_code"] for i in c.get("/api/v1/dashboard/widgets/caseload/data", headers=H(nt)).get_json()["data"]]
          and all(n["patient"]["id"] != P3 for n in c.get("/api/v1/notifications?status=all&per_page=100", headers=H(nt)).get_json()["data"]))
    r = c.post(f"/api/v1/patients/{P3}/nurse-assignments", json={"nurse_id": NURSE2, "is_primary": True}, headers=H(at))
    check("admin assigns the new nurse", r.status_code == 201)
    check("new nurse: can read the patient immediately", [c.get(u, headers=H(t2)).status_code for u in urls[:-1]] == [200] * (len(urls) - 1))
    nl2 = c.get(f"/api/v1/notifications?patient_id={P3}&status=open", headers=H(t2)).get_json()["data"]
    check("new nurse sees the open alert (acknowledged by the previous nurse) and can continue it",
          [n["id"] for n in nl2] and nl2[0]["status"] == "acknowledged"
          and c.post(f"/api/v1/notifications/{nl2[0]['id']}/start", headers=H(t2)).status_code == 200)
    rows_after = {m.__name__: sorted((r.id, r.cycle_id, r.cycle_day, str(getattr(r, "record_status"))) for r in db.session.query(m).all())
                  for m in (SymptomRecord, VitalSign, LabResult, MedicationRecord)}
    check("history untouched by assignment changes (symptom / vital / lab / medication rows, cycle_id, cycle_day)", rows_before == rows_after)
    check("historical cycle_day values unchanged after a timezone update", (
        c.patch(f"/api/v1/patients/{p1.public_id}", json={"timezone": "Asia/Tokyo"}, headers=H(nt)).status_code == 200
        and {m.__name__: sorted((r.id, r.cycle_id, r.cycle_day) for r in db.session.query(m).filter_by(patient_id=p1.id)) for m in (SymptomRecord, VitalSign, LabResult, MedicationRecord)}
        == {k: sorted((i, cid, cd) for i, cid, cd, _s in v if db.session.get({"SymptomRecord": SymptomRecord, "VitalSign": VitalSign, "LabResult": LabResult,
                                                                            "MedicationRecord": MedicationRecord}[k], i).patient_id == p1.id) for k, v in rows_after.items()}))
    c.patch(f"/api/v1/patients/{p1.public_id}", json={"timezone": "Asia/Taipei"}, headers=H(nt))

    # ================================================================ account for an existing patient
    r = c.post(f"/api/v1/patients/{P5}/account", json={"email": "patient.wu@demo.local"}, headers=H(at))
    d = r.get_json()["data"]
    check("admin creates an account for an existing patient → temporary password once", r.status_code == 201 and TEMP_RE.match(d["temporary_password"]))
    secrets_seen.append(d["temporary_password"])
    check("second account for the same patient → 409", err(c.post(f"/api/v1/patients/{P5}/account", json={"email": "other@demo.local"}, headers=H(at)))[:2] == (409, "CONFLICT"))
    det5 = c.get(f"/api/v1/patients/{P5}", headers=H(at)).get_json()["data"]
    check("detail shows account state, never the password", det5["account"]["must_change_password"] is True and d["temporary_password"] not in json.dumps(det5))

    # ================================================================ audit
    logs = db.session.query(AuditLog).all()
    blob = json.dumps([[l.action, l.resource_type, l.changes] for l in logs], ensure_ascii=False)
    check("no temporary or chosen password appears anywhere in audit logs", not any(s in blob for s in secrets_seen + ["ChenNurse2026", "Ding2026pass"]))
    by = lambda action, rt: [l for l in logs if l.action == action and l.resource_type == rt]  # noqa: E731
    check("audit: CREATE patient_profiles ×4, CREATE users (accounts + nurse) ×3", len(by("CREATE", "patient_profiles")) == 4 and len(by("CREATE", "users")) == 3)
    check("audit: UPDATE patient_profiles records field names only", by("UPDATE", "patient_profiles")
          and all(set(l.changes) == {"fields"} for l in by("UPDATE", "patient_profiles")) and "海鮮" not in blob)
    check("audit: ASSIGN ×2 and assignment end (UPDATE ended) with actor = admin", len(by("ASSIGN", "nurse_patient_assignments")) == 2
          and any(l.changes.get("ended") for l in by("UPDATE", "nurse_patient_assignments"))
          and all(l.actor_role == "admin" for l in by("ASSIGN", "nurse_patient_assignments")))
    check("audit: password changes recorded without values", len([l for l in by("UPDATE", "users") if l.changes.get("password") == "changed"]) == 2)
    check("audit: care alert and diagnosis writes", by("CREATE", "patient_care_alerts") and by("UPDATE", "patient_care_alerts") and by("CREATE", "cancer_diagnoses"))
    check("audit: detail views recorded (VIEW patient_profiles)", any(l.action == "VIEW" and l.resource_type == "patient_profiles" for l in logs))

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
