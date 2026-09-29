"""Sprint 3: treatment schedule — appointments with preparation steps, check-in / complete /
cancel / reschedule (history kept), 今日行程 on the dashboard, nurse today list, timeline
APPOINTMENT events, infusion appointments from plans, cycle delay moving appointments,
permissions, privacy, audit."""
import sys
from pathlib import Path
import json
import warnings
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from app import create_app
from app.extensions import db
from app.models import Appointment, AuditLog, PatientProfile, User
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
    return {"Authorization": f"Bearer {t}"}


def err(r):
    j = r.get_json() or {}
    return r.status_code, j.get("error", {}).get("code"), [d.get("field") for d in j.get("error", {}).get("details", [])]


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


TPE = timezone(timedelta(hours=8))
now = datetime.now(TPE)
today = now.date()


def at_local(day, hh, mm=0):
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=TPE)


with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()
    at, nt, pt = token("admin01@demo.local"), token("nurse01@demo.local"), token("patient01@demo.local")
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    P1 = p1.public_id
    seed_count = db.session.query(Appointment).filter_by(patient_id=p1.id).count()
    seed_ids = {a.id for a in db.session.query(Appointment).all()}

    # ================================================================ create
    one_hour_ago = now - timedelta(seconds=10)  # "earlier today" (stays today except in the first seconds after midnight)
    body = {"patient_id": P1, "appointment_type": "lab_draw", "title": "化療前抽血", "scheduled_at": iso(one_hour_ago),
            "duration_min": 15, "location": "一樓檢驗科", "notes": "內部備註：請先確認血管狀況",
            "instructions": [{"instruction_type": "fasting", "due_at": iso(one_hour_ago - timedelta(hours=8)), "text": "前一晚起空腹"},
                             {"instruction_type": "check_in", "due_at": iso(one_hour_ago - timedelta(minutes=10)), "text": "提前 10 分鐘報到"}]}
    check("admin cannot create appointments (nurse only) → 403", c.post("/api/v1/chemotherapy/appointments", json=body, headers=H(at)).status_code == 403)
    check("patient cannot create appointments → 403", c.post("/api/v1/chemotherapy/appointments", json=body, headers=H(pt)).status_code == 403)
    check("validation: type, title, time, instruction", set(err(c.post("/api/v1/chemotherapy/appointments", json={
        "patient_id": P1, "appointment_type": "x", "title": "", "scheduled_at": "2026-01-01T00:00:00",
        "instructions": [{"instruction_type": "y", "text": ""}]}, headers=H(nt)))[2])
          >= {"appointment_type", "title", "scheduled_at", "instructions[0].instruction_type", "instructions[0].text"})
    check("time more than a year ahead → 400", "scheduled_at" in err(c.post("/api/v1/chemotherapy/appointments", json={
        **body, "scheduled_at": iso(now + timedelta(days=400))}, headers=H(nt)))[2])
    check("cycle of another patient → 400", "cycle_id" in err(c.post("/api/v1/chemotherapy/appointments", json={**body, "cycle_id": 99999}, headers=H(nt)))[2])
    r = c.post("/api/v1/chemotherapy/appointments", json=body, headers=H(nt))
    A = r.get_json()["data"]
    check("nurse creates an appointment with preparation steps", r.status_code == 201 and A["status"] == "scheduled"
          and [i["instruction_type"] for i in A["instructions"]] == ["fasting", "check_in"] and A["notes"].startswith("內部備註"))

    # ================================================================ dashboard / patient view
    ts = c.get("/api/v1/dashboard/patient/me", headers=H(pt)).get_json()["data"]["widgets"]["today-schedule"]
    check("patient 今日行程 shows it with its preparation steps", any(a["id"] == A["id"] for a in ts["appointments"])
          and any(i["appointment_id"] == A["id"] and i["text"] == "提前 10 分鐘報到" for i in ts["highlight_instructions"]))
    mine = c.get("/api/v1/chemotherapy/appointments?patient_id=me", headers=H(pt)).get_json()["data"]
    pa = next(a for a in mine if a["id"] == A["id"])
    check("patient sees own appointments without internal notes / creator", "notes" not in pa and "created_by" not in pa)
    today_list = c.get("/api/v1/dashboard/widgets/today-appointments/data", headers=H(nt)).get_json()
    check("nurse today list includes it (patient code + name)", any(i["id"] == A["id"] and i["patient_code"] == "P00001" for i in today_list["data"])
          and today_list["meta"]["total"] == len(today_list["data"]))
    check("today list is nurse only", c.get("/api/v1/dashboard/widgets/today-appointments/data", headers=H(pt)).status_code == 403)
    day = c.get(f"/api/v1/chemotherapy/appointments?patient_id={P1}&date={today.isoformat()}", headers=H(nt)).get_json()["data"]
    check("filter by local date", A["id"] in [a["id"] for a in day] and all(a["scheduled_at"] for a in day))
    check("bad date filter → 400", c.get(f"/api/v1/chemotherapy/appointments?patient_id={P1}&date=2026-99-01", headers=H(nt)).status_code == 400)
    check("patient_id required", c.get("/api/v1/chemotherapy/appointments", headers=H(nt)).status_code == 400)

    # ================================================================ update / check-in / complete / timeline
    r = c.patch(f"/api/v1/chemotherapy/appointments/{A['id']}", json={"location": "二樓抽血站", "instructions": [
        {"instruction_type": "check_in", "text": "提前 15 分鐘報到"}]}, headers=H(nt))
    check("update details and replace preparation steps", r.get_json()["data"]["location"] == "二樓抽血站"
          and [i["text"] for i in r.get_json()["data"]["instructions"]] == ["提前 15 分鐘報到"])
    check("time cannot be edited (use reschedule)", "scheduled_at" in err(c.patch(f"/api/v1/chemotherapy/appointments/{A['id']}", json={"scheduled_at": iso(now)}, headers=H(nt)))[2])
    check("complete before check-in is allowed; check-in first here", c.post(f"/api/v1/chemotherapy/appointments/{A['id']}/check-in", headers=H(nt)).get_json()["data"]["status"] == "checked_in")
    check("check in twice → 409", err(c.post(f"/api/v1/chemotherapy/appointments/{A['id']}/check-in", headers=H(nt)))[:2] == (409, "INVALID_STATE"))
    ts = c.get("/api/v1/dashboard/patient/me", headers=H(pt)).get_json()["data"]["widgets"]["today-schedule"]
    check("today-schedule shows the checked-in status", next(a for a in ts["appointments"] if a["id"] == A["id"])["status"] == "checked_in")
    check("complete → completed", c.post(f"/api/v1/chemotherapy/appointments/{A['id']}/complete", headers=H(nt)).get_json()["data"]["status"] == "completed")
    check("completed appointment cannot be edited / cancelled", c.patch(f"/api/v1/chemotherapy/appointments/{A['id']}", json={"title": "x"}, headers=H(nt)).status_code == 409
          and c.post(f"/api/v1/chemotherapy/appointments/{A['id']}/cancel", json={"reason": "x"}, headers=H(nt)).status_code == 409)
    tl = c.get("/api/v1/patients/me/timeline", headers=H(pt)).get_json()
    ev = next((e for e in tl["data"] if e["event_id"] == f"APPOINTMENT:{A['id']}"), None)
    check("timeline: APPOINTMENT event for the attended appointment", ev and ev["event_type"] == "APPOINTMENT" and ev["detail"]["status_text"] == "已完成"
          and ev["summary"] == "已完成，二樓抽血站" and "APPOINTMENT" in tl["meta"]["event_types"], ev)
    check("patient timeline: no internal notes on appointments", "notes" not in ev["detail"])
    stl = c.get(f"/api/v1/patients/{P1}/timeline", headers=H(nt)).get_json()["data"]
    check("staff timeline: appointment notes visible", next(e for e in stl if e["event_id"] == f"APPOINTMENT:{A['id']}")["detail"]["notes"].startswith("內部備註"))
    check("existing 7 event types unchanged in the timeline", {e["event_type"] for e in stl} >= {"CHEMOTHERAPY", "SYMPTOM", "VITAL_SIGN", "LAB_RESULT"})

    # future appointment: not in the history yet; check-in on a future day refused
    tomorrow = at_local(today + timedelta(days=1), 9)
    F = c.post("/api/v1/chemotherapy/appointments", json={**body, "title": "門診追蹤", "appointment_type": "clinic_visit",
                                                          "scheduled_at": iso(tomorrow), "instructions": []}, headers=H(nt)).get_json()["data"]
    check("future appointment not in the timeline yet", all(e["event_id"] != f"APPOINTMENT:{F['id']}" for e in c.get("/api/v1/patients/me/timeline", headers=H(pt)).get_json()["data"]))
    check("check-in on a future day → 409", c.post(f"/api/v1/chemotherapy/appointments/{F['id']}/check-in", headers=H(nt)).status_code == 409)

    # ================================================================ reschedule keeps the history
    check("reschedule needs a reason", "reason" in err(c.post(f"/api/v1/chemotherapy/appointments/{F['id']}/reschedule", json={"scheduled_at": iso(tomorrow + timedelta(days=2))}, headers=H(nt)))[2])
    r = c.post(f"/api/v1/chemotherapy/appointments/{F['id']}/reschedule", json={"scheduled_at": iso(tomorrow + timedelta(days=2)), "reason": "病人請假"}, headers=H(nt))
    N = r.get_json()["data"]
    old = c.get(f"/api/v1/chemotherapy/appointments/{F['id']}", headers=H(nt)).get_json()["data"]
    check("reschedule → new appointment linked to the original; original rescheduled", r.status_code == 201 and N["rescheduled_from_id"] == F["id"]
          and old["status"] == "rescheduled" and old["rescheduled_to_id"] == N["id"] and old["scheduled_at"] == F["scheduled_at"])
    check("rescheduled original cannot be rescheduled again → 409", c.post(f"/api/v1/chemotherapy/appointments/{F['id']}/reschedule", json={"scheduled_at": iso(tomorrow), "reason": "x"}, headers=H(nt)).status_code == 409)
    # a past appointment rescheduled to today: timeline shows the move both ways
    P = c.post("/api/v1/chemotherapy/appointments", json={**body, "title": "衛教", "appointment_type": "education_session",
                                                          "scheduled_at": iso(now - timedelta(days=2)), "instructions": [
                                                              {"instruction_type": "bring_item", "due_at": iso(now - timedelta(days=2, hours=1)), "text": "攜帶藥袋"}]}, headers=H(nt)).get_json()["data"]
    P2 = c.post(f"/api/v1/chemotherapy/appointments/{P['id']}/reschedule", json={"scheduled_at": iso(now - timedelta(minutes=30)), "reason": "改到今天"}, headers=H(nt)).get_json()["data"]
    check("reschedule moves preparation steps by the same time", P2["instructions"][0]["text"] == "攜帶藥袋"
          and datetime.fromisoformat(P2["instructions"][0]["due_at"].replace("Z", "+00:00")) == datetime.fromisoformat(P2["scheduled_at"].replace("Z", "+00:00")) - timedelta(hours=1))
    tl = {e["event_id"]: e for e in c.get("/api/v1/patients/me/timeline", headers=H(pt)).get_json()["data"]}
    check("timeline: original shows 已改期 → new time; new one shows where it came from",
          tl[f"APPOINTMENT:{P['id']}"]["detail"]["status_text"] == "已改期" and tl[f"APPOINTMENT:{P['id']}"]["detail"]["rescheduled_to"]["id"] == P2["id"]
          and tl[f"APPOINTMENT:{P2['id']}"]["detail"]["rescheduled_from"]["id"] == P["id"])
    ts = c.get("/api/v1/dashboard/patient/me", headers=H(pt)).get_json()["data"]["widgets"]["today-schedule"]
    check("今日行程 shows the new one; rescheduled originals hidden", P2["id"] in [a["id"] for a in ts["appointments"]] and P["id"] not in [a["id"] for a in ts["appointments"]])

    # cancel
    check("cancel needs a reason", "reason" in err(c.post(f"/api/v1/chemotherapy/appointments/{N['id']}/cancel", json={}, headers=H(nt)))[2])
    check("cancel → cancelled", c.post(f"/api/v1/chemotherapy/appointments/{N['id']}/cancel", json={"reason": "醫師停診"}, headers=H(nt)).get_json()["data"]["status"] == "cancelled")

    # ================================================================ plan infusions + cycle delay moves them
    pf = c.get("/api/v1/chemotherapy/regimens", headers=H(nt)).get_json()["data"][0]
    newp = c.post("/api/v1/patients", json={"display_name": "行程測試", "gender": "male", "date_of_birth": "1965-05-05"}, headers=H(at)).get_json()["data"]
    PID = newp["id"]
    c.post(f"/api/v1/patients/{PID}/nurse-assignments", json={"nurse_id": nurse.public_id}, headers=H(at))
    dx = c.post(f"/api/v1/patients/{PID}/diagnoses", json={"cancer_type_code": "C11", "diagnosis_date": "2026-08-01"}, headers=H(nt)).get_json()["data"]
    start = today + timedelta(days=2)
    plan_body = {"patient_id": PID, "diagnosis_id": dx["id"], "regimen_id": pf["id"], "plan_name": "排程測試", "total_cycles": 3,
                 "start_date": start.isoformat(), "generate_infusion_appointments": {"enabled": True, "time": "09:30", "location": "日間化療室"}}
    check("infusion time must be HH:MM", "generate_infusion_appointments.time" in err(c.post("/api/v1/chemotherapy/plans", json={
        **plan_body, "generate_infusion_appointments": {"enabled": True, "time": "9am"}}, headers=H(nt)))[2])
    plan = c.post("/api/v1/chemotherapy/plans", json=plan_body, headers=H(nt)).get_json()["data"]
    appts = c.get(f"/api/v1/chemotherapy/appointments?patient_id={PID}", headers=H(nt)).get_json()["data"]
    check("plan creates one chemo_infusion appointment per cycle at 09:30 local", len(appts) == 3
          and all(a["appointment_type"] == "chemo_infusion" and a["location"] == "日間化療室" for a in appts)
          and appts[0]["scheduled_at"] == iso(at_local(start, 9, 30)) and appts[0]["cycle_number"] == 1
          and [c_["appointment_id"] for c_ in plan["cycles"]] == [a["id"] for a in appts], [a["scheduled_at"] for a in appts])
    c2 = plan["cycles"][1]
    r = c.post(f"/api/v1/chemotherapy/cycles/{c2['id']}/delay", json={"new_scheduled_date": (start + timedelta(days=24)).isoformat(),
                                                                     "delay_reason": "ANC 過低", "reschedule_appointments": True}, headers=H(nt))
    moved = c.get(f"/api/v1/chemotherapy/appointments?patient_id={PID}", headers=H(nt)).get_json()["data"]
    orig2 = next(a for a in moved if a["id"] == appts[1]["id"])
    new2 = next(a for a in moved if a["rescheduled_from_id"] == appts[1]["id"])
    check("cycle delay with reschedule_appointments moves the infusion by the same days (history kept)",
          r.status_code == 200 and orig2["status"] == "rescheduled" and new2["scheduled_at"] == iso(at_local(start + timedelta(days=24), 9, 30))
          and r.get_json()["data"]["appointment_id"] == new2["id"])
    check("other cycles' appointments untouched", next(a for a in moved if a["id"] == appts[2]["id"])["status"] == "scheduled")

    # ================================================================ permissions / privacy
    other = User(role=nurse.role, email="n9@demo.local", password_hash=nurse.password_hash, display_name="其他護理師", password_changed_at=nurse.password_changed_at)
    db.session.add(other)
    db.session.commit()
    ot = token("n9@demo.local")
    codes = [c.get(f"/api/v1/chemotherapy/appointments?patient_id={P1}", headers=H(ot)).status_code,
             c.get(f"/api/v1/chemotherapy/appointments/{A['id']}", headers=H(ot)).status_code,
             c.post(f"/api/v1/chemotherapy/appointments/{P2['id']}/check-in", headers=H(ot)).status_code,
             c.post("/api/v1/chemotherapy/appointments", json=body, headers=H(ot)).status_code]
    check("unassigned nurse: reads and writes → 404", codes == [404] * 4, codes)
    check("unassigned nurse: nothing in the today list", c.get("/api/v1/dashboard/widgets/today-appointments/data", headers=H(ot)).get_json()["data"] == [])
    pt2 = token("patient01@demo.local")
    check("patient cannot read another patient's appointments", c.get(f"/api/v1/chemotherapy/appointments?patient_id={PID}", headers=H(pt2)).status_code == 404
          and c.get(f"/api/v1/chemotherapy/appointments/{appts[0]['id']}", headers=H(pt2)).status_code == 404)
    check("admin reads any appointment", c.get(f"/api/v1/chemotherapy/appointments/{appts[0]['id']}", headers=H(at)).status_code == 200)
    check("seed appointments untouched", db.session.query(Appointment).filter_by(patient_id=p1.id).count() == seed_count + 5)

    # ================================================================ audit
    logs = [l for l in db.session.query(AuditLog).all() if l.resource_type == "appointments"]
    statuses = [l.changes.get("status") for l in logs if l.action == "UPDATE"]
    check("audit: create / update / check-in / complete / cancel / reschedule", sum(l.action == "CREATE" for l in logs) == 9
          and {"checked_in", "completed", "cancelled", "rescheduled"} <= set(statuses))
    check("audit: reschedule records the reason and the new id", any(l.changes.get("reason") == "病人請假" and l.changes.get("rescheduled_to_id") == N["id"] for l in logs))
    created = {a.id for a in db.session.query(Appointment).all()} - seed_ids
    check("audit: every appointment created here has a CREATE entry (incl. plan infusions and moved ones)",
          {int(l.resource_id) for l in logs if l.action == "CREATE"} == created and len(created) == 9, sorted(created))
    check("audit: cycle delay records the moved appointments", any(l.changes.get("reason") == "cycle delayed" for l in logs)
          and any(l.resource_type == "chemotherapy_cycles" and l.changes.get("rescheduled_appointments") == [[appts[1]["id"], new2["id"]]]
                  for l in db.session.query(AuditLog).all() if l.changes))
    check("no password in audit", "Demo@1234" not in json.dumps([l.changes for l in db.session.query(AuditLog).all()], ensure_ascii=False))

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
