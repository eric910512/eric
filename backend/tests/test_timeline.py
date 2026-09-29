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
from app.models import (AuditLog, MedicationRecord, Notification, NursePatientAssignment, NursingAssessment, PatientProfile,
                        Role, SymptomRecord, User)
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


def tl(t, pid="me", **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return c.get(f"/api/v1/patients/{pid}/timeline?{q}", headers=H(t))


def body(t, pid="me", **params):
    return tl(t, pid, **params).get_json()


def all_events(t, pid="me", page=100, **params):
    events, cursor = [], None
    while True:
        b = body(t, pid, limit=page, **({"cursor": cursor} if cursor else {}), **params)
        events += b["data"]
        if not b["meta"]["has_more"]:
            return events
        cursor = b["meta"]["next_cursor"]


def err(r):
    j = r.get_json() or {}
    return r.status_code, j.get("error", {}).get("code"), [d["field"] for d in j.get("error", {}).get("details", [])]


with app.app_context():
    db.create_all()
    seed_dev_data()
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    nurse2 = User(role=roles["nurse"], email="nurse02@demo.local", password_hash=pw, display_name="測試護理師 陳", password_changed_at=utcnow())  # not assigned to P00001
    admin = User(role=roles["admin"], email="admin@demo.local", password_hash=pw, display_name="管理者", password_changed_at=utcnow())
    u2 = User(role=roles["patient"], email="p2@demo.local", password_hash=pw, display_name="乙", password_changed_at=utcnow())
    p2 = PatientProfile(patient_code="P00002", display_name="測試病人 乙", date_of_birth=date(1960, 1, 1), user=u2)
    db.session.add_all([nurse2, admin, p2])
    db.session.flush()
    db.session.add(NursePatientAssignment(nurse_id=nurse2.id, patient_id=p2.id))
    med = db.session.query(MedicationRecord).filter_by(patient_id=p1.id).first()
    med.reaction_notes = "輸注中輕微潮紅，減速後緩解（內部紀錄）"
    db.session.commit()
    pt, nt, nt2, at, p2t = (token(e) for e in ("patient01@demo.local", "nurse01@demo.local", "nurse02@demo.local",
                                               "admin@demo.local", "p2@demo.local"))
    P1, P2 = p1.public_id, p2.public_id

    # ---- generate events: fever + tachycardia → 2 alerts; full lifecycle on fever; quick resolve on tachycardia
    v = c.post("/api/v1/vital-signs", json={"patient_id": "me", "temperature_c": 38.6, "heart_rate_bpm": 126},
               headers=H(pt, str(uuid.uuid4()))).get_json()["data"]
    fever = db.session.query(Notification).filter_by(recipient_id=nurse.id, source_id=v["id"]).join(Notification.alert_rule).filter_by(code="fever").one()
    tachy = db.session.query(Notification).filter_by(recipient_id=nurse.id, source_id=v["id"]).join(Notification.alert_rule).filter_by(code="tachycardia").one()
    NOTE = "已電話聯繫病人，建議立即至急診（內部備註）"
    c.post(f"/api/v1/notifications/{fever.id}/acknowledge", headers=H(nt))
    c.post(f"/api/v1/notifications/{fever.id}/start", headers=H(nt))
    c.post(f"/api/v1/notifications/{fever.id}/resolve", json={"resolution_note": NOTE}, headers=H(nt))
    c.patch(f"/api/v1/notifications/{tachy.id}/resolve", json={"resolution_note": "已衛教休息後複測（內部）"}, headers=H(nt))
    # symptom review → signed nursing assessment (plan = internal action note)
    rec = db.session.query(SymptomRecord).filter_by(patient_id=p1.id).order_by(SymptomRecord.recorded_at.desc()).first()
    PLAN = "評估疲倦與噁心，調整止吐藥時間（內部處置）"
    c.post(f"/api/v1/symptoms/records/{rec.id}/review", json={"assessment_type": "phone_follow_up", "action_note": PLAN,
                                                               "risk_level": "medium"}, headers=H(nt))
    # draft assessment: staff only
    db.session.add(NursingAssessment(patient_id=p1.id, assessed_by=nurse.id, assessed_at=utcnow() - timedelta(minutes=5),
                                     assessment_type="follow_up", plan="草稿：待主治醫師確認（內部）", risk_level="high",
                                     sign_status="draft", source="nurse"))
    db.session.commit()
    # lab panel with an abnormal ANC
    c.post("/api/v1/labs/results", json={"patient_id": P1, "collected_at": v["measured_at"],
                                         "results": [{"test_code": "WBC", "value": 2.1}, {"test_code": "ANC", "value": 0.8}]}, headers=H(nt))

    # ---------------- permissions ----------------
    check("patient: own timeline via me → 200", tl(pt).status_code == 200)
    check("patient: own timeline via public id → 200", tl(pt, P1).status_code == 200)
    check("patient: another patient's timeline → 404", tl(pt, P2).status_code == 404)
    check("nurse: assigned patient → 200", tl(nt, P1).status_code == 200)
    check("nurse: unassigned patient → 404", tl(nt2, P1).status_code == 404)
    check("nurse: 'me' is not a patient → 404", tl(nt, "me").status_code == 404)
    check("admin: any patient → 200", tl(at, P1).status_code == 200 and tl(at, P2).status_code == 200)
    check("no token → 401", c.get(f"/api/v1/patients/{P1}/timeline").status_code == 401)

    # ---------------- mixed event types + schema ----------------
    staff = all_events(nt, P1)
    types = {e["event_type"] for e in staff}
    seven = {"CHEMOTHERAPY", "SYMPTOM", "VITAL_SIGN", "LAB_RESULT", "NOTIFICATION", "NOTIFICATION_STATUS", "NURSING_ASSESSMENT"}
    # Sprint 3 added APPOINTMENT: the seed's appointment (today 14:00) appears once its time has come.
    check("nurse timeline mixes all 7 original event types (+ APPOINTMENT when due)", seven <= types <= seven | {"APPOINTMENT"}, sorted(types))
    required = {"event_id", "event_type", "occurred_at", "title", "summary", "severity", "source_id", "cycle_id", "cycle_day"}
    check("every event has the required fields; event_id unique", all(required <= set(e) for e in staff)
          and len({e["event_id"] for e in staff}) == len(staff))
    chemo = [e for e in staff if e["event_type"] == "CHEMOTHERAPY"]
    start = next(e for e in chemo if e["source"]["table"] == "chemotherapy_cycles")
    med_ev = next(e for e in chemo if e["source"]["table"] == "medication_records")
    check("chemotherapy: cycle start (all-day, Day 1) + medication administration", start["all_day"] and start["cycle_day"] == 1
          and start["title"] == "第 1 次化療開始" and "Cisplatin" in med_ev["summary"] and med_ev["cycle_day"] == 1, (start["title"], med_ev["summary"]))
    lab = next(e for e in staff if e["event_type"] == "LAB_RESULT" and e["severity"])
    check("lab: one event per panel (2 rows), worst flag as severity", len(lab["source"]["ids"]) == 2 and lab["severity"] == "warning"
          and "ANC 0.8 L" in lab["summary"], lab["summary"])
    vit = next(e for e in staff if e["event_type"] == "VITAL_SIGN" and e["source_id"] == v["id"])
    check("vital: summary + critical severity from reference flags", "體溫 38.6°C" in vit["summary"] and vit["severity"] == "critical")
    n_ev = next(e for e in staff if e["event_type"] == "NOTIFICATION" and e["source_id"] == fever.id)
    check("notification: cycle_day derived for events without one; staff message", n_ev["cycle_day"] == 4 and "P00001" in n_ev["summary"]
          and n_ev["detail"]["recommended_action"])

    # ---------------- ordering ----------------
    times = [e["occurred_at"] for e in staff]
    check("newest → oldest", times == sorted(times, reverse=True), times[:5])
    pos = {e["event_id"]: i for i, e in enumerate(staff)}
    check("alert listed above the vital sign that raised it",
          pos[f"NOTIFICATION:{fever.id}"] < pos[f"VITAL_SIGN:{v['id']}"] and pos[f"NOTIFICATION:{tachy.id}"] < pos[f"VITAL_SIGN:{v['id']}"])

    # ---------------- status changes ----------------
    steps = [e for e in staff if e["event_type"] == "NOTIFICATION_STATUS" and e["source_id"] == fever.id]
    check("full lifecycle → 3 status events (acknowledged, in_progress, resolved)",
          sorted(e["detail"]["status"] for e in steps) == ["acknowledged", "in_progress", "resolved"], [e["title"] for e in steps])
    quick = [e for e in staff if e["event_type"] == "NOTIFICATION_STATUS" and e["source_id"] == tachy.id]
    check("quick resolve (steps at the same instant) → collapsed into one 'resolved' event", [e["detail"]["status"] for e in quick] == ["resolved"])
    res = next(e for e in steps if e["detail"]["status"] == "resolved")
    check("staff sees who resolved it and the internal note", res["detail"]["by"] == "測試護理師 林" and res["detail"]["resolution_note"] == NOTE)

    # ---------------- pagination ----------------
    paged = all_events(nt, P1, page=3)
    check("cursor pagination (limit=3) returns the same events in the same order, no duplicates",
          [e["event_id"] for e in paged] == [e["event_id"] for e in staff], f"{len(paged)} vs {len(staff)}")
    b = body(nt, P1, limit=3)
    check("page meta: limit, has_more, next_cursor", b["meta"]["limit"] == 3 and b["meta"]["returned"] == 3 and b["meta"]["has_more"]
          and b["meta"]["next_cursor"])
    last = body(nt, P1, limit=100)
    check("last page: has_more false, next_cursor null", not last["meta"]["has_more"] and last["meta"]["next_cursor"] is None)
    new_first = body(nt, P1, limit=3)["data"][0]["event_id"]
    c.post("/api/v1/vital-signs", json={"patient_id": "me", "heart_rate_bpm": 80}, headers=H(pt, str(uuid.uuid4())))
    page2 = body(nt, P1, limit=3, cursor=b["meta"]["next_cursor"])["data"]
    check("keyset cursor stays stable when a newer event arrives", page2[0]["event_id"] == staff[3]["event_id"] and new_first == staff[0]["event_id"])
    staff = all_events(nt, P1)

    # ---------------- date filtering ----------------
    d1 = start["occurred_at"]  # local midnight of Day 1, in UTC
    cycle_day1 = (p1.chemotherapy_cycles[0].actual_start_date).isoformat()
    day = all_events(nt, P1, start_date=cycle_day1, end_date=cycle_day1)
    check("date filter (Day 1 only, local dates): cycle start, medication, pre-chemo CBC",
          {e["event_type"] for e in day} == {"CHEMOTHERAPY", "LAB_RESULT"} and len(day) == 3 and all(e["cycle_day"] == 1 for e in day),
          [(e["event_type"], e["title"]) for e in day])
    today = (utcnow() + timedelta(hours=8)).date().isoformat()
    only_today = all_events(nt, P1, start_date=today)
    check("start_date only: today's events, none older", only_today and all(e["occurred_at"] >= d1 for e in only_today)
          and all(e["cycle_day"] == 4 for e in only_today if e["cycle_day"] is not None))
    check("end_date only: nothing after it", all(e["occurred_at"] < only_today[-1]["occurred_at"] for e in all_events(nt, P1, end_date=cycle_day1)))
    future = (date.fromisoformat(today) + timedelta(days=30)).isoformat()
    check("date range in the future → empty", body(nt, P1, start_date=future)["data"] == [])

    # ---------------- validation ----------------
    check("bad date → 400 start_date", err(tl(nt, P1, start_date="2026-13-01"))[2] == ["start_date"])
    check("start after end → 400 end_date", err(tl(nt, P1, start_date="2026-09-25", end_date="2026-09-01"))[2] == ["end_date"])
    check("limit 0 / 101 → 400", err(tl(nt, P1, limit=0))[0] == 400 and err(tl(nt, P1, limit=101))[0] == 400)
    check("bad cursor → 400", err(tl(nt, P1, cursor="not-a-cursor"))[2] == ["cursor"])

    # ---------------- empty timeline ----------------
    e = body(p2t)
    check("patient with no records → empty list, has_more false", e["data"] == [] and not e["meta"]["has_more"])

    # ---------------- patient privacy ----------------
    pat = all_events(pt)
    raw = json.dumps(pat, ensure_ascii=False)
    check("patient: internal resolution notes never appear", NOTE not in raw and "已衛教休息後複測" not in raw)
    check("patient: no staff names anywhere", "測試護理師" not in raw)
    check("patient: nursing assessment plan / risk / drafts hidden", PLAN not in raw and "草稿" not in raw and "risk_level" not in raw)
    check("patient: medication reaction notes hidden", "潮紅" not in raw and "reaction_notes" not in raw)
    check("patient: lab items without reference-range numbers", all("ref_low" not in i for x in pat if x["event_type"] == "LAB_RESULT" for i in x["detail"]["items"]))
    pn = next(x for x in pat if x["event_type"] == "NOTIFICATION" and "體溫" in x["summary"])
    check("patient: notification = own copy (patient wording), patient status text",
          pn["summary"].startswith("您的體溫") and pn["detail"]["status_text"] == "已處理完成" and "recommended_action" not in pn["detail"])
    ps = [x for x in pat if x["event_type"] == "NOTIFICATION_STATUS"]
    check("patient: status changes shown in patient wording only",
          {x["title"] for x in ps} == {"護理師已接手", "護理師正在處理", "已處理完成"} and all("by" not in x["detail"] and "resolution_note" not in x["detail"] for x in ps),
          sorted({x["title"] for x in ps}))
    pa = [x for x in pat if x["event_type"] == "NURSING_ASSESSMENT"]
    check("patient: signed assessment shown as 護理師電話追蹤 with generic text", len(pa) == 1 and pa[0]["title"] == "護理師電話追蹤"
          and pa[0]["summary"] == "護理師已追蹤您的狀況。" and pa[0]["severity"] is None)
    check("patient: same clinical events as staff for own records (symptoms, vitals, labs, chemo)",
          {x["event_id"] for x in pat if x["event_type"] in ("SYMPTOM", "VITAL_SIGN", "LAB_RESULT", "CHEMOTHERAPY")}
          == {x["event_id"] for x in staff if x["event_type"] in ("SYMPTOM", "VITAL_SIGN", "LAB_RESULT", "CHEMOTHERAPY")})
    sa = [x for x in staff if x["event_type"] == "NURSING_ASSESSMENT"]
    check("staff: signed + draft assessments with plan and risk severity", len(sa) == 2 and any(PLAN in x["summary"] for x in sa)
          and any(x["title"].endswith("草稿") and x["severity"] == "critical" for x in sa))
    check("staff: symptom detail includes reviewer", any(x["detail"].get("reviewed_by") == "測試護理師 林" for x in staff if x["event_type"] == "SYMPTOM"))

    # ---------------- rapid lifecycle (clock resolution) ----------------
    # Steps taken back to back must stay separate even when the system clock has not advanced
    # (Windows ~15 ms); only quick resolve collapses into one step.
    rule = fever.alert_rule
    rapid = []
    for i in range(15):
        ek = f"test:rapid:{i}"
        for rid in (p1.user_id, nurse.id):
            db.session.add(Notification(recipient_id=rid, patient_id=p1.id, alert_rule_id=rule.id, event_key=ek, type="risk_alert",
                                        severity="warning", title=f"快速測試 {i}", message="x", sent_at=utcnow()))
        db.session.commit()
        rapid.append(db.session.query(Notification).filter_by(event_key=ek, recipient_id=nurse.id).one())
    for n in rapid:
        c.post(f"/api/v1/notifications/{n.id}/acknowledge", headers=H(nt))
        c.post(f"/api/v1/notifications/{n.id}/start", headers=H(nt))
        c.post(f"/api/v1/notifications/{n.id}/resolve", json={"resolution_note": "快速完成"}, headers=H(nt))
    db.session.expire_all()
    rows = db.session.query(Notification).filter(Notification.event_key.like("test:rapid:%")).all()
    check("rapid acknowledge → start → resolve: step times strictly increasing on every copy (30 rows)",
          len(rows) == 30 and all(r.acknowledged_at < r.started_at < r.resolved_at for r in rows))
    ids = {n.id for n in rapid}
    steps_by_id = {}
    for e in all_events(nt, P1):
        if e["event_type"] == "NOTIFICATION_STATUS" and e["source_id"] in ids:
            steps_by_id.setdefault(e["source_id"], set()).add(e["detail"]["status"])
    check("rapid lifecycle: timeline keeps all 3 steps for each of the 15 alerts",
          len(steps_by_id) == 15 and all(v == {"acknowledged", "in_progress", "resolved"} for v in steps_by_id.values()),
          sorted(len(v) for v in steps_by_id.values()))

    # ---------------- same second (frozen clock) ----------------
    # ACKNOWLEDGED, IN_PROGRESS and RESOLVED created within one second — here all three calls even
    # read the very same instant — must keep their order on the timeline, at ms precision.
    import re
    from datetime import datetime as _dt
    from unittest.mock import patch

    import app.modules.notification.services as notif_services

    MS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    frozen = _dt(2026, 9, 25, 3, 0, 0, 500000)  # one fixed instant
    same = []
    for i, ek in enumerate(("test:same-second:full", "test:same-second:quick")):
        for rid in (p1.user_id, nurse.id):
            db.session.add(Notification(recipient_id=rid, patient_id=p1.id, alert_rule_id=fever.alert_rule_id, event_key=ek, type="risk_alert",
                                        severity="warning", title=f"同秒測試 {i}", message="x", sent_at=frozen, created_at=frozen))
        db.session.commit()
        same.append(db.session.query(Notification).filter_by(event_key=ek, recipient_id=nurse.id).one())
    full, quick = same
    with patch.object(notif_services, "utcnow", lambda: frozen):
        codes = [c.post(f"/api/v1/notifications/{full.id}/{a}", json={"resolution_note": "同秒完成"} if a == "resolve" else None, headers=H(nt)).status_code
                 for a in ("acknowledge", "start", "resolve")]
        codes.append(c.post(f"/api/v1/notifications/{quick.id}/acknowledge", headers=H(nt)).status_code)
        codes.append(c.patch(f"/api/v1/notifications/{quick.id}/resolve", json={"resolution_note": "快速處理"}, headers=H(nt)).status_code)
    check("same-second lifecycle calls all succeed", codes == [200] * 5, codes)
    detail = c.get(f"/api/v1/notifications/{full.id}", headers=H(nt)).get_json()["data"]["handling"]
    ack, start, res = (detail[k]["at"] for k in ("acknowledged", "started", "resolved"))
    check("API step times: ms precision, strictly increasing, all within the same second",
          all(MS.match(t) for t in (ack, start, res)) and ack < start < res and ack[:19] == start[:19] == res[:19], (ack, start, res))
    events = all_events(nt, P1, start_date="2026-09-25", end_date="2026-09-25")
    steps = [e for e in events if e["event_type"] == "NOTIFICATION_STATUS" and e["source_id"] == full.id]
    check("timeline keeps ACKNOWLEDGED, IN_PROGRESS, RESOLVED as three events, newest first",
          [e["detail"]["status"] for e in steps] == ["resolved", "in_progress", "acknowledged"]
          and [e["occurred_at"] for e in steps] == [res, start, ack], [(e["detail"]["status"], e["occurred_at"]) for e in steps])
    n_ev = next(e for e in events if e["event_type"] == "NOTIFICATION" and e["source_id"] == full.id)
    check("the alert itself (created at the same instant as the first step) is listed below its steps",
          events.index(n_ev) > max(events.index(e) for e in steps))
    qsteps = [e["detail"]["status"] for e in events if e["event_type"] == "NOTIFICATION_STATUS" and e["source_id"] == quick.id]
    check("quick resolve after an acknowledge in the same instant: 'acknowledged' kept, quick steps collapse to 'resolved'",
          qsteps == ["resolved", "acknowledged"], qsteps)
    pat_steps = [e for e in all_events(pt, start_date="2026-09-25", end_date="2026-09-25")
                 if e["event_type"] == "NOTIFICATION_STATUS" and e["detail"]["notification_id"] in {n.id for n in db.session.query(Notification).filter_by(event_key="test:same-second:full")}]
    check("patient timeline shows the same three steps in the same order (patient wording)",
          [e["title"] for e in pat_steps] == ["已處理完成", "護理師正在處理", "護理師已接手"], [e["title"] for e in pat_steps])
    every = all_events(nt, P1)
    check("every timeline timestamp uses the ms format (…:SS.sssZ)", all(MS.match(e["occurred_at"]) for e in every))

    # ---------------- audit ----------------
    before = db.session.query(AuditLog).filter_by(resource_type="patient_timeline").count()
    tl(nt, P1)
    tl(pt)
    after = db.session.query(AuditLog).filter_by(resource_type="patient_timeline").order_by(AuditLog.id).all()
    check("staff views are audited (VIEW), patient self-views are not", len(after) - before == 1 and after[-1].action == "VIEW"
          and after[-1].actor_user_id == nurse.id and after[-1].patient_id == p1.id)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
