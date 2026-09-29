import sys
from pathlib import Path
import uuid
import warnings
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import (ChemotherapyCycle, ChemotherapyPlan, CancerDiagnosis, Notification, NursePatientAssignment,
                        PatientProfile, Role, User, VitalSign)
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


def dash(t, pid="me"):
    return c.get(f"/api/v1/dashboard/patient/{pid}", headers=H(t)).get_json()["data"]["widgets"]


def caseload(t, sort=None):
    r = c.get("/api/v1/dashboard/widgets/caseload/data" + (f"?sort={sort}" if sort else ""), headers=H(t))
    return r.get_json()


def symptoms(t, pid="me", pain=2, fever=False):
    body = {"patient_id": pid, "form_code": "daily_chemo_check", "values": [
        {"definition_code": "pain", "value_numeric": pain}, {"definition_code": "nausea", "value_numeric": 3},
        {"definition_code": "fatigue", "value_numeric": 4}, {"definition_code": "fever", "value_boolean": fever}]}
    return c.post("/api/v1/symptoms/records", json=body, headers=H(t, str(uuid.uuid4()))).get_json()["data"]


def vitals(t, pid="me", **fields):
    return c.post("/api/v1/vital-signs", json={"patient_id": pid, **fields}, headers=H(t, str(uuid.uuid4()))).get_json()["data"]


with app.app_context():
    db.create_all()
    seed_dev_data()
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    # Two more assigned patients with an active cycle, for ranking
    ct = p1.diagnoses[0].cancer_type
    extra = {}
    for code, name, email in [("P00002", "測試病人 乙", "p2@demo.local"), ("P00003", "測試病人 丙", "p3@demo.local")]:
        u = User(role=roles["patient"], email=email, password_hash=pw, display_name=name, password_changed_at=utcnow())
        pp = PatientProfile(patient_code=code, display_name=name, date_of_birth=date(1960, 1, 1), user=u)
        dx = CancerDiagnosis(patient=pp, cancer_type=ct, diagnosis_date=date(2026, 1, 1))
        start = utcnow().date() - timedelta(days=3)
        plan = ChemotherapyPlan(patient=pp, diagnosis=dx, start_date=start, status="active", total_cycles=3)
        ChemotherapyCycle(plan=plan, patient=pp, cycle_number=1, scheduled_date=start, actual_start_date=start, status="in_progress")
        db.session.add_all([pp, NursePatientAssignment(nurse=nurse, patient=pp)])
        extra[code] = pp
    db.session.commit()
    pt, nt = token("patient01@demo.local"), token("nurse01@demo.local")
    p2t, p3t = token("p2@demo.local"), token("p3@demo.local")

    # ---------------- patient widget, baseline ----------------
    w = dash(pt)
    rs = w["risk-summary"]
    check("patient dashboard includes risk-summary", rs is not None and rs["date"] == w["today-schedule"]["date"])
    check("patients no longer receive the internal nurse-view block", "nurse-view" not in w, list(w))
    nw = dash(nt, p1.public_id)
    check("nurse still receives nurse-view and risk-summary", "nurse-view" in nw and "risk-summary" in nw)
    check("risk-summary level == risk engine level (nurse-view), not recomputed",
          rs["level"] == nw["nurse-view"]["risk"]["level"], (rs["level"], nw["nurse-view"]["risk"]["level"]))
    check("baseline: nothing recorded today → report + measure actions",
          not rs["today"]["symptom_reported"] and not rs["today"]["vitals_recorded"]
          and [a["code"] for a in rs["actions"]] == ["report_symptoms", "measure_vitals"], rs["actions"])

    # ---------------- today's records flow into the summary ----------------
    symptoms(pt, pain=5)
    rs = dash(pt)["risk-summary"]
    check("after a symptom report: symptom_reported, worst scores listed, action removed",
          rs["today"]["symptom_reported"] and rs["today"]["symptoms"][0] == {"label": "疼痛", "score": 5.0}
          and [a["code"] for a in rs["actions"]] == ["measure_vitals"], rs["today"]["symptoms"])
    check("low risk → status stable wording", rs["status"] == "stable" and rs["title"] == "今天狀況穩定", (rs["level"], rs["status"]))

    vitals(pt, temperature_c=38.5, heart_rate_bpm=88)
    rs = dash(pt)["risk-summary"]
    nv = dash(nt, p1.public_id)["nurse-view"]
    check("fever → engine level high; patient status urgent with call_hotline first",
          rs["level"] == "high" == nv["risk"]["level"] and rs["status"] == "urgent" and rs["actions"][0]["code"] == "call_hotline"
          and rs["title"] == "請立即聯絡醫療團隊", rs["actions"])
    check("today's alerts come from the patient's notifications (open, critical)",
          rs["today"]["alerts"]["total"] == 1 and rs["today"]["alerts"]["open"] == 1 and rs["today"]["alerts"]["critical"] == 1
          and rs["today"]["alerts"]["items"][0]["title"] == "化療期間發燒")
    check("latest vitals today with flags", rs["today"]["vitals_recorded"] and rs["today"]["vital_flags"][0]["level"] == "critical")
    check("patient reasons are the engine reasons, open alerts reworded",
          any(r.startswith("體溫 38.5") for r in rs["reasons"]) and "化療期間發燒（護理團隊處理中）" in rs["reasons"], rs["reasons"])

    # nurse resolves → patient sees "handled"; level unchanged (engine still counts the 72h critical vital)
    n = db.session.query(Notification).filter_by(recipient_id=nurse.id, source_table="vital_signs").one()
    c.patch(f"/api/v1/notifications/{n.id}/resolve", json={"resolution_note": "已電話聯繫，請至急診評估"}, headers=H(nt))
    rs = dash(pt)["risk-summary"]
    check("after nurse resolves: status handled, nurse note hidden from patient, level unchanged (high)",
          rs["status"] == "handled" and rs["level"] == "high" and rs["today"]["alerts"]["open"] == 0
          and "resolution_note" not in rs["today"]["alerts"]["items"][0] and rs["today"]["alerts"]["items"][0]["status_text"] == "已處理完成"
          and "call_hotline" not in [a["code"] for a in rs["actions"]], rs["title"])
    check("internal assessment reasons hidden from patients",
          not any(r.startswith("護理評估風險") for r in rs["reasons"]))

    # high level, critical handled but a warning still open → attention, not urgent
    vitals(pt, heart_rate_bpm=125)                           # tachycardia (warning), fever still high in engine
    rs = dash(pt)["risk-summary"]
    check("high level + only a warning open → status attention (not urgent); level still high",
          rs["level"] == "high" and rs["status"] == "attention" and rs["today"]["alerts"]["open"] == 1
          and "call_hotline" not in [a["code"] for a in rs["actions"]], (rs["level"], rs["status"], rs["title"]))

    # ---------------- nurse caseload ranking ----------------
    symptoms(p2t, pain=8)                                   # P00002: medium (symptom ≥7) + open warning alert
    vitals(p3t, spo2_pct=89)                                 # P00003: high + open critical alert
    body = caseload(nt)
    order = [(i["priority_rank"], i["patient_code"], i["risk_level"], i["unacknowledged_critical_count"]) for i in body["data"]]
    check("default sort=risk: high with open critical alert first, then high (handled), then medium",
          [x[1] for x in order] == ["P00003", "P00001", "P00002"] and body["meta"]["sort"] == "risk", order)
    check("each row has priority_rank 1..n and ranking fields",
          [i["priority_rank"] for i in body["data"]] == [1, 2, 3]
          and all("latest_alert_at" in i and "unacknowledged_critical_count" in i for i in body["data"]))
    check("caseload risk levels == each patient's risk engine level",
          all(i["risk_level"] == dash(nt, i["patient_id"])["nurse-view"]["risk"]["level"] for i in body["data"]))
    check("meta counts by level", body["meta"]["high"] == 2 and body["meta"]["medium"] == 1 and body["meta"]["low"] == 0, body["meta"])

    # tie-break: two high patients — the one with more open critical alerts wins; equal → newest open alert
    vitals(pt, spo2_pct=88)                                  # P00001 now also has an open critical alert (newer)
    order = [i["patient_code"] for i in caseload(nt)["data"]]
    check("tie on level + critical count → newest open alert first", order[:2] == ["P00001", "P00003"], order)

    check("sort=name → by patient code", [i["patient_code"] for i in caseload(nt, "name")["data"]] == ["P00001", "P00002", "P00003"])
    lr = caseload(nt, "last_report")["data"]
    check("sort=last_report → longest without report first",
          [i["hours_since_last_report"] for i in lr] == sorted([i["hours_since_last_report"] for i in lr], reverse=True))
    check("invalid sort → 400", c.get("/api/v1/dashboard/widgets/caseload/data?sort=x", headers=H(nt)).status_code == 400)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
