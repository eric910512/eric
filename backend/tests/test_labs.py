import sys
from pathlib import Path
import uuid
import warnings
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import (AuditLog, CancerDiagnosis, ChemotherapyCycle, ChemotherapyPlan, LabResult, Notification,
                        NursePatientAssignment, PatientProfile, Role, User)
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


def H(t, key=None):
    return {"Authorization": f"Bearer {t}", **({"Idempotency-Key": key} if key else {})}


def iso(dt):
    return dt.replace(microsecond=0).isoformat() + "Z"


def post(t, pid, results, collected=None, key=None, **extra):
    body = {"patient_id": pid, "collected_at": iso(collected or utcnow() - timedelta(hours=1)), "results": results, **extra}
    return c.post("/api/v1/labs/results", json=body, headers=H(t, key))


def err(r):
    j = r.get_json() or {}
    return r.status_code, j.get("error", {}).get("code"), [d["field"] for d in j.get("error", {}).get("details", [])]


def cbc(wbc=6.1, anc=3.2, hgb=12.5, plt=230):
    return [{"test_code": "WBC", "value": wbc}, {"test_code": "ANC", "value": anc},
            {"test_code": "HGB", "value": hgb}, {"test_code": "PLT", "value": plt}]


with app.app_context():
    db.create_all()
    seed_dev_data()
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    # P00002: assigned, active cycle (for the medium-risk case); P00003: not assigned
    ct = p1.diagnoses[0].cancer_type
    u2 = User(role=roles["patient"], email="p2@demo.local", password_hash=pw, display_name="乙", password_changed_at=utcnow())
    p2 = PatientProfile(patient_code="P00002", display_name="測試病人 乙", date_of_birth=date(1960, 1, 1), user=u2)
    dx = CancerDiagnosis(patient=p2, cancer_type=ct, diagnosis_date=date(2026, 1, 1))
    start = utcnow().date() - timedelta(days=3)
    plan = ChemotherapyPlan(patient=p2, diagnosis=dx, start_date=start, status="active", total_cycles=3)
    ChemotherapyCycle(plan=plan, patient=p2, cycle_number=1, scheduled_date=start, actual_start_date=start, status="in_progress")
    p3 = PatientProfile(patient_code="P00003", display_name="測試病人 丙", date_of_birth=date(1960, 1, 1))
    db.session.add_all([p2, p3, NursePatientAssignment(nurse=nurse, patient=p2),
                        User(role=roles["admin"], email="admin@demo.local", password_hash=pw, display_name="管理者", password_changed_at=utcnow())])
    db.session.commit()
    pt, nt, at, p2t = token("patient01@demo.local"), token("nurse01@demo.local"), token("admin@demo.local"), token("p2@demo.local")
    P1, P2, P3 = p1.public_id, p2.public_id, p3.public_id

    # ---------------- test types ----------------
    tt = c.get("/api/v1/labs/test-types", headers=H(pt)).get_json()["data"]
    check("4 supported tests with units and ranges", [t["code"] for t in tt] == ["WBC", "ANC", "HGB", "PLT"]
          and next(t for t in tt if t["code"] == "ANC")["critical_low"] == 0.5, [(t["code"], t["unit"]) for t in tt])

    # ---------------- normal panel ----------------
    r = post(nt, P1, cbc(anc=3.214))
    d = r.get_json()["data"]
    check("nurse records a CBC → 201, 4 rows, cycle_day 4, all N, ANC rounded to 2 dp",
          r.status_code == 201 and len(d["results"]) == 4 and d["cycle_day"] == 4
          and all(x["abnormal_flag"] == "N" for x in d["results"]) and next(x for x in d["results"] if x["test_code"] == "ANC")["value"] == 3.21)
    check("reference range snapshot + unit on each row; no alerts", d["results"][0]["ref_low"] == 4.0 and d["results"][0]["unit"] == "10³/µL"
          and d["triggered_alerts"] == [])
    audit = db.session.query(AuditLog).filter_by(action="CREATE", resource_type="lab_results").order_by(AuditLog.id.desc()).first()
    check("audit CREATE lists ids and test codes (no values)", audit.changes["test_codes"] == ["WBC", "ANC", "HGB", "PLT"]
          and "3.21" not in str(audit.changes) and audit.actor_role == "nurse")

    # ---------------- ANC alerts ----------------
    d = post(nt, P1, [{"test_code": "ANC", "value": 0.8}]).get_json()["data"]
    check("ANC 0.8 → neutropenia warning only; flag L",
          [(a["alert_rule_code"], a["severity"]) for a in d["triggered_alerts"]] == [("neutropenia", "warning")]
          and d["results"][0]["abnormal_flag"] == "L")
    notes = db.session.query(Notification).filter_by(source_table="lab_results", source_id=d["results"][0]["id"]).all()
    patient_note = next(n for n in notes if n.recipient_id == p1.user_id)
    check("notifications to patient + nurse; patient wording uses friendly name",
          len(notes) == 2 and "嗜中性白血球（抵抗力）" in patient_note.message and "0.8" in patient_note.message, patient_note.message)
    d = post(nt, P1, [{"test_code": "ANC", "value": 0.4}, {"test_code": "PLT", "value": 15}]).get_json()["data"]
    check("ANC 0.4 → severe_neutropenia critical only (value_above stops the warning rule)",
          [(a["alert_rule_code"], a["severity"]) for a in d["triggered_alerts"]] == [("severe_neutropenia", "critical")], d["triggered_alerts"])
    check("flags: ANC LL, PLT 15 LL (no PLT rule → no alert)",
          {x["test_code"]: x["abnormal_flag"] for x in d["results"]} == {"ANC": "LL", "PLT": "LL"})
    severe_row_id = next(x["id"] for x in d["results"] if x["test_code"] == "ANC")

    # ---------------- validation ----------------
    for label, kwargs, field in [
        ("missing collected_at", {"results": cbc(), "collected": None, "_drop": True}, "collected_at"),
        ("collected in the future", {"results": cbc(), "collected": utcnow() + timedelta(hours=2)}, "collected_at"),
        ("collected 31 days ago", {"results": cbc(), "collected": utcnow() - timedelta(days=31)}, "collected_at"),
        ("unknown test", {"results": [{"test_code": "CRP", "value": 1}]}, "results[0].test_code"),
        ("duplicate test", {"results": [{"test_code": "ANC", "value": 1}, {"test_code": "ANC", "value": 2}]}, "results[1].test_code"),
        ("string value", {"results": [{"test_code": "ANC", "value": "1.2"}]}, "results[0].value"),
        ("out of range", {"results": [{"test_code": "HGB", "value": 40}]}, "results[0].value"),
        ("empty results", {"results": []}, "results"),
    ]:
        if kwargs.pop("_drop", False):
            r = c.post("/api/v1/labs/results", json={"patient_id": P1, "results": kwargs["results"]}, headers=H(nt))
        else:
            r = post(nt, P1, kwargs["results"], collected=kwargs.get("collected"))
        status, code, fields = err(r)
        check(f"400 {label}", status == 400 and code == "VALIDATION_ERROR" and field in fields, fields)
    r = post(nt, P1, cbc(), resulted_at=iso(utcnow() - timedelta(days=2)))
    check("400 resulted_at before collected_at", "resulted_at" in err(r)[2])

    # ---------------- idempotency ----------------
    key = str(uuid.uuid4())
    body_results = [{"test_code": "ANC", "value": 0.45}]
    collected = utcnow() - timedelta(minutes=30)
    r1 = post(nt, P1, body_results, collected=collected, key=key)
    n_mid = db.session.query(Notification).count()
    r2 = post(nt, P1, body_results, collected=collected, key=key)
    check("replay → same row ids, Idempotent-Replayed, same alerts, no new notifications",
          [x["id"] for x in r1.get_json()["data"]["results"]] == [x["id"] for x in r2.get_json()["data"]["results"]]
          and r2.headers.get("Idempotent-Replayed") == "true" and db.session.query(Notification).count() == n_mid
          and r2.get_json()["data"]["triggered_alerts"][0]["alert_rule_code"] == "severe_neutropenia", r2.get_json()["data"]["triggered_alerts"])
    check("same key, different body → 422", err(post(nt, P1, [{"test_code": "ANC", "value": 2.0}], collected=collected, key=key))[:2] == (422, "IDEMPOTENCY_KEY_MISMATCH"))

    # ---------------- authorization ----------------
    check("patient cannot enter labs → 403", err(post(pt, "me", cbc()))[:2] == (403, "FORBIDDEN"))
    check("admin cannot enter labs → 403", post(at, P1, cbc()).status_code == 403)
    check("nurse → unassigned patient 404", post(nt, P3, cbc()).status_code == 404)
    check("patient → full history 403", c.get("/api/v1/labs/results/me", headers=H(pt)).status_code == 403)

    # ---------------- nurse full history ----------------
    body = c.get(f"/api/v1/labs/results/{P1}", headers=H(nt)).get_json()
    data = body["data"]
    check("full history: latest per test = newest value, with flag and ref range",
          data["latest"]["ANC"]["value"] == 0.45 and data["latest"]["ANC"]["abnormal_flag"] == "LL"
          and data["latest"]["ANC"]["ref_low"] == 1.5 and data["latest"]["HGB"]["value"] == 12.5)
    anc_series = [pt_["value"] for pt_ in data["series"]["ANC"]]
    check("series oldest → newest (seed 3.8 first, 0.45 last)", anc_series[0] == 3.8 and anc_series[-1] == 0.45, anc_series)
    check("ANC 0.45 within cooldown of 0.4 alert → no new notification on that row", data["latest"]["ANC"]["alerts"] == [])
    severe_note = db.session.query(Notification).filter_by(source_table="lab_results", source_id=severe_row_id,
                                                          recipient_id=nurse.id).one()
    alert = {"my_notification_id": severe_note.id}
    check("filter test=PLT → only PLT rows", {x["test_code"] for x in c.get(f"/api/v1/labs/results/{P1}?test=PLT", headers=H(nt)).get_json()["data"]["results"]} == {"PLT"})
    check("days=0 → 400", c.get(f"/api/v1/labs/results/{P1}?days=0", headers=H(nt)).status_code == 400)
    check("admin can read full history", c.get(f"/api/v1/labs/results/{P1}", headers=H(at)).status_code == 200)

    # resolve the lab alert through the existing notification workflow
    r = c.patch(f"/api/v1/notifications/{alert['my_notification_id']}/resolve", json={"resolution_note": "已通知醫師，安排門診評估"}, headers=H(nt))
    check("lab alert resolvable via PATCH /notifications/{id}/resolve", r.status_code == 200)

    # ---------------- patient simplified summary ----------------
    s = c.get("/api/v1/labs/summary/me", headers=H(pt)).get_json()["data"]
    anc = next(i for i in s["items"] if i["code"] == "ANC")
    check("patient summary: friendly labels, value, status word, no reference-range numbers",
          anc["label"] == "嗜中性白血球（抵抗力）" and anc["value"] == 0.45 and anc["status_text"] == "過低"
          and "ref_low" not in anc and "critical_low" not in anc, anc)
    check("patient summary explains what low ANC / platelets mean", "發燒" in anc["explanation"]
          and "出血" in next(i for i in s["items"] if i["code"] == "PLT")["explanation"])
    check("overall message flags critical values", "特別注意" in s["message"], s["message"])
    check("nurse can view the simplified summary too", c.get(f"/api/v1/labs/summary/{P1}", headers=H(nt)).status_code == 200)
    w = c.get("/api/v1/dashboard/patient/me", headers=H(pt)).get_json()["data"]["widgets"]
    check("patient dashboard includes lab-summary widget", w["lab-summary"]["items"][0]["code"] == "WBC")

    # ---------------- risk engine integration ----------------
    nv = c.get(f"/api/v1/dashboard/patient/{P1}", headers=H(nt)).get_json()["data"]["widgets"]["nurse-view"]
    check("risk engine: critical ANC within 7 days → high with ANC reason",
          nv["risk"]["level"] == "high" and any(r.startswith("ANC 0.45") and "嚴重偏低" in r for r in nv["risk"]["reasons"]), nv["risk"]["reasons"])
    check("patient risk-summary shows the same lab reason", any(r.startswith("ANC 0.45") for r in w["risk-summary"]["reasons"]))
    post(nt, P2, [{"test_code": "PLT", "value": 120}])
    nv2 = c.get(f"/api/v1/dashboard/patient/{P2}", headers=H(nt)).get_json()["data"]["widgets"]["nurse-view"]
    check("platelets below range (no alert rule) → medium via risk engine",
          nv2["risk"]["level"] == "medium" and any(r.startswith("血小板 120") for r in nv2["risk"]["reasons"]), nv2["risk"])
    case = {i["patient_code"]: i for i in c.get("/api/v1/dashboard/widgets/caseload/data", headers=H(nt)).get_json()["data"]}
    check("caseload reflects lab-driven risk (P00001 high, P00002 medium)", case["P00001"]["risk_level"] == "high" and case["P00002"]["risk_level"] == "medium")
    # labs older than 7 days are ignored by the risk engine
    for row in db.session.query(LabResult).filter_by(patient_id=p2.id):
        row.collected_at = utcnow() - timedelta(days=8)
    db.session.commit()
    nv2 = c.get(f"/api/v1/dashboard/patient/{P2}", headers=H(nt)).get_json()["data"]["widgets"]["nurse-view"]
    check("labs older than 7 days no longer raise risk", not any("血小板" in r for r in nv2["risk"]["reasons"]), nv2["risk"])

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
