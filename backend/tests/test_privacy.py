"""Privacy regression: what patients can see, and cross-patient access for nurses / admin.

Fills P00001 with every kind of internal information, then sweeps every endpoint a patient
can call and asserts none of it leaks; then checks every patient-scoped endpoint for scope.
"""
import json
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
from app.models import MedicationRecord, Notification, NursePatientAssignment, NursingAssessment, PatientProfile, Role, SymptomRecord, User
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


def get(t, url):
    return c.get(url, headers=H(t))


with app.app_context():
    db.create_all()
    seed_dev_data()
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    nurse2 = User(role=roles["nurse"], email="nurse02@demo.local", password_hash=pw, display_name="測試護理師 陳", password_changed_at=utcnow())
    admin = User(role=roles["admin"], email="admin@demo.local", password_hash=pw, display_name="管理者", password_changed_at=utcnow())
    u2 = User(role=roles["patient"], email="p2@demo.local", password_hash=pw, display_name="乙", password_changed_at=utcnow())
    p2 = PatientProfile(patient_code="P00002", display_name="測試病人 乙", date_of_birth=date(1960, 1, 1), user=u2)
    db.session.add_all([nurse2, admin, p2])
    db.session.flush()
    db.session.add(NursePatientAssignment(nurse_id=nurse2.id, patient_id=p2.id))  # nurse2: only P00002
    db.session.commit()
    pt, nt, nt2, at, p2t = (token(e) for e in ("patient01@demo.local", "nurse01@demo.local", "nurse02@demo.local",
                                               "admin@demo.local", "p2@demo.local"))
    P1, P2 = p1.public_id, p2.public_id

    # ---------------- fill P00001 with internal information ----------------
    SECRETS = {
        "resolution note": "內部備註：已通知主治醫師調整療程",
        "quick-resolve note": "內部備註：電話衛教後複測",
        "review action note / plan": "內部處置：暫停下一次化療等待評估",
        "draft SOAP subjective": "草稿主觀：病人情緒低落",
        "draft SOAP objective": "草稿客觀：面色蒼白",
        "draft SOAP assessment": "草稿評估：疑似感染",
        "draft plan": "草稿計畫：安排住院",
        "medication reaction notes": "給藥反應：輸注中輕微潮紅",
        "nurse name": "測試護理師",
    }
    v = c.post("/api/v1/vital-signs", json={"patient_id": "me", "temperature_c": 38.7, "heart_rate_bpm": 125},
               headers=H(pt, str(uuid.uuid4()))).get_json()["data"]
    fever = db.session.query(Notification).filter_by(recipient_id=nurse.id, source_id=v["id"]).join(Notification.alert_rule).filter_by(code="fever").one()
    tachy = db.session.query(Notification).filter_by(recipient_id=nurse.id, source_id=v["id"]).join(Notification.alert_rule).filter_by(code="tachycardia").one()
    for step in ("acknowledge", "start"):
        c.post(f"/api/v1/notifications/{fever.id}/{step}", headers=H(nt))
    c.post(f"/api/v1/notifications/{fever.id}/resolve", json={"resolution_note": SECRETS["resolution note"]}, headers=H(nt))
    c.patch(f"/api/v1/notifications/{tachy.id}/resolve", json={"resolution_note": SECRETS["quick-resolve note"]}, headers=H(nt))
    rec = db.session.query(SymptomRecord).filter_by(patient_id=p1.id).order_by(SymptomRecord.recorded_at.desc()).first()
    c.post(f"/api/v1/symptoms/records/{rec.id}/review", json={"assessment_type": "phone_follow_up", "risk_level": "high",
                                                               "action_note": SECRETS["review action note / plan"]}, headers=H(nt))
    db.session.add(NursingAssessment(patient_id=p1.id, assessed_by=nurse.id, assessed_at=utcnow() - timedelta(minutes=3),
                                     assessment_type="follow_up", sign_status="draft", source="nurse", risk_level="high",
                                     subjective=SECRETS["draft SOAP subjective"], objective=SECRETS["draft SOAP objective"],
                                     assessment=SECRETS["draft SOAP assessment"], plan=SECRETS["draft plan"]))
    db.session.query(MedicationRecord).filter_by(patient_id=p1.id).first().reaction_notes = SECRETS["medication reaction notes"]
    db.session.commit()
    c.post("/api/v1/labs/results", json={"patient_id": P1, "collected_at": v["measured_at"], "results": [{"test_code": "ANC", "value": 0.4}]}, headers=H(nt))
    lab_alert = db.session.query(Notification).filter_by(source_table="lab_results", recipient_id=nurse.id).first()
    c.post(f"/api/v1/notifications/{lab_alert.id}/acknowledge", headers=H(nt))

    # symptom record entered by a nurse on the patient's behalf (reporter = nurse)
    c.post("/api/v1/symptoms/records", json={"patient_id": P1, "form_code": "daily_chemo_check", "values": [
        {"definition_code": "pain", "value_numeric": 2}, {"definition_code": "nausea", "value_numeric": 1},
        {"definition_code": "fatigue", "value_numeric": 3}, {"definition_code": "fever", "value_boolean": False}]},
        headers=H(nt, str(uuid.uuid4())))

    # ---------------- patient sweep ----------------
    patient_copies = db.session.query(Notification).filter_by(recipient_id=p1.user_id).all()
    pages, cursor = [], None
    while True:
        b = get(pt, "/api/v1/patients/me/timeline?limit=5" + (f"&cursor={cursor}" if cursor else "")).get_json()
        pages.append(b)
        if not b["meta"]["has_more"]:
            break
        cursor = b["meta"]["next_cursor"]
    responses = {
        "dashboard": get(pt, "/api/v1/dashboard/patient/me").get_json(),
        "notifications list": get(pt, "/api/v1/notifications?per_page=100").get_json(),
        "notification details": [get(pt, f"/api/v1/notifications/{n.id}").get_json() for n in patient_copies],
        "timeline (all pages)": pages,
        "lab summary": get(pt, "/api/v1/labs/summary/me").get_json(),
        "symptom records": get(pt, "/api/v1/symptoms/records/me?review_status=all").get_json(),
        "test types": get(pt, "/api/v1/labs/test-types").get_json(),
    }
    check("patient sweep covered 7 endpoint groups, timeline fully paged", len(responses) == 7 and len(pages) > 1, f"{len(pages)} timeline pages")
    for name, resp in responses.items():
        raw = json.dumps(resp, ensure_ascii=False)
        leaked = [k for k, s in SECRETS.items() if s in raw]
        check(f"patient · {name}: no nurse names / notes / SOAP / drafts / reaction notes", not leaked, leaked)
        bad_keys = [k for k in ("resolution_note", "risk_level", "recommended_action", "reaction_notes", "subjective", "objective",
                                '"plan"', "nurse-view", "assessed_by", "allowed_actions", "trigger", "source_record") if k in raw]
        check(f"patient · {name}: no internal fields", not bad_keys, bad_keys)
    by_nurse = [r for r in responses["symptom records"]["data"] if r["source"] == "nurse"]
    check("patient · record entered by a nurse: reported_by hidden, own records keep the patient as reporter",
          by_nurse and all(r["reported_by"] is None for r in by_nurse)
          and all(r["reported_by"]["display_name"] == "測試病人 甲" for r in responses["symptom records"]["data"] if r["source"] == "patient_app"))
    rs = responses["dashboard"]["data"]["widgets"]["risk-summary"]
    check("patient · risk-summary reasons exclude internal assessment judgements", not any(r.startswith("護理評估") for r in rs["reasons"]), rs["reasons"])
    tl_items = [e for p in pages for e in p["data"]]
    check("patient · timeline shows no draft assessment and no assessment severity",
          [e["title"] for e in tl_items if e["event_type"] == "NURSING_ASSESSMENT"] == ["護理師電話追蹤"]
          and all(e["severity"] is None for e in tl_items if e["event_type"] == "NURSING_ASSESSMENT"))
    check("patient · cannot call staff-only endpoints (full labs, abnormal vitals, lifecycle, review)",
          get(pt, "/api/v1/labs/results/me").status_code == 403 and get(pt, "/api/v1/vital-signs/abnormal").status_code == 403
          and c.post(f"/api/v1/notifications/{patient_copies[0].id}/acknowledge", headers=H(pt)).status_code == 403
          and c.post(f"/api/v1/symptoms/records/{rec.id}/review", json={"action_note": "x"}, headers=H(pt)).status_code == 403)
    check("patient · cannot read another patient (timeline, dashboard, labs, records, nurse copy of own alert)",
          get(pt, f"/api/v1/patients/{P2}/timeline").status_code == 404 and get(pt, f"/api/v1/dashboard/patient/{P2}").status_code == 404
          and get(pt, f"/api/v1/labs/summary/{P2}").status_code == 404 and get(pt, f"/api/v1/symptoms/records/{P2}").status_code == 404
          and get(pt, f"/api/v1/notifications/{fever.id}").status_code == 404)

    # ---------------- nurse scope ----------------
    scoped = {
        "dashboard": f"/api/v1/dashboard/patient/{P1}",
        "timeline": f"/api/v1/patients/{P1}/timeline",
        "full labs": f"/api/v1/labs/results/{P1}",
        "lab summary": f"/api/v1/labs/summary/{P1}",
        "symptom records": f"/api/v1/symptoms/records/{P1}",
        "notification detail": f"/api/v1/notifications/{fever.id}",
    }
    for name, url in scoped.items():
        check(f"nurse (assigned) · {name} → 200; unassigned nurse → 404; admin → 200",
              get(nt, url).status_code == 200 and get(nt2, url).status_code == 404 and get(at, url).status_code == 200,
              (get(nt, url).status_code, get(nt2, url).status_code, get(at, url).status_code))
    check("unassigned nurse · cannot write for P00001 (vitals, labs, review, lifecycle)",
          c.post("/api/v1/vital-signs", json={"patient_id": P1, "heart_rate_bpm": 80}, headers=H(nt2, str(uuid.uuid4()))).status_code == 404
          and c.post("/api/v1/labs/results", json={"patient_id": P1, "collected_at": v["measured_at"], "results": [{"test_code": "ANC", "value": 2}]}, headers=H(nt2)).status_code == 404
          and c.post(f"/api/v1/symptoms/records/{rec.id}/review", json={"action_note": "x"}, headers=H(nt2)).status_code == 404
          and c.post(f"/api/v1/notifications/{lab_alert.id}/start", headers=H(nt2)).status_code == 404)
    lists = [get(nt2, u).get_json()["data"] for u in ("/api/v1/notifications?per_page=100", "/api/v1/dashboard/widgets/caseload/data")]
    check("unassigned nurse · lists (notifications, caseload) contain no P00001 data",
          "P00001" not in json.dumps(lists, ensure_ascii=False) and not get(nt2, "/api/v1/vital-signs/abnormal").get_json()["data"])
    staff_raw = json.dumps(get(nt, f"/api/v1/patients/{P1}/timeline?limit=100").get_json(), ensure_ascii=False)
    check("assigned nurse · timeline includes the internal information patients cannot see",
          all(SECRETS[k] in staff_raw for k in ("resolution note", "review action note / plan", "draft plan", "medication reaction notes")))
    check("admin · sees every patient's alerts in the notification list",
          {n["patient"]["patient_code"] for n in get(at, "/api/v1/notifications?status=all&per_page=100").get_json()["data"]} >= {"P00001"})

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
