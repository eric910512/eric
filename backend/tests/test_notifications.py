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
from app.models import AuditLog, Notification, NursePatientAssignment, PatientProfile, Role, User
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


def err(r):
    j = r.get_json() or {}
    return r.status_code, j.get("error", {}).get("code")


def vitals(t, pid="me", **fields):
    return c.post("/api/v1/vital-signs", json={"patient_id": pid, **fields}, headers=H(t, str(uuid.uuid4()))).get_json()["data"]


def listing(t, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = c.get(f"/api/v1/notifications?{q}", headers=H(t))
    return r.get_json()


def act(t, nid, action, **body):
    return c.post(f"/api/v1/notifications/{nid}/{action}", json=body or None, headers=H(t))


def copies(event_key):
    db.session.expire_all()
    return db.session.query(Notification).filter_by(event_key=event_key).all()


def last_audit():
    return db.session.query(AuditLog).filter_by(resource_type="notifications").order_by(AuditLog.id.desc()).first()


with app.app_context():
    db.create_all()
    seed_dev_data()
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    nurse2 = User(role=roles["nurse"], email="nurse02@demo.local", password_hash=pw, display_name="測試護理師 陳", password_changed_at=utcnow())
    nurse3 = User(role=roles["nurse"], email="nurse03@demo.local", password_hash=pw, display_name="測試護理師 王", password_changed_at=utcnow())  # not assigned
    admin = User(role=roles["admin"], email="admin@demo.local", password_hash=pw, display_name="管理者", password_changed_at=utcnow())
    u2 = User(role=roles["patient"], email="p2@demo.local", password_hash=pw, display_name="乙", password_changed_at=utcnow())
    p2 = PatientProfile(patient_code="P00002", display_name="測試病人 乙", date_of_birth=date(1960, 1, 1), user=u2)
    db.session.add_all([nurse2, nurse3, admin, p2])
    db.session.flush()
    db.session.add_all([NursePatientAssignment(nurse_id=nurse2.id, patient_id=p1.id),
                        NursePatientAssignment(nurse_id=nurse3.id, patient_id=p2.id)])  # nurse3 only has P00002
    db.session.commit()
    pt, nt, nt2, nt3, at, p2t = (token(e) for e in ("patient01@demo.local", "nurse01@demo.local", "nurse02@demo.local",
                                                   "nurse03@demo.local", "admin@demo.local", "p2@demo.local"))

    # fever (critical) + tachycardia (warning) for P00001; low SpO2 for P00002 (nurse3's patient)
    v = vitals(pt, temperature_c=38.6, heart_rate_bpm=126)
    vitals(p2t, spo2_pct=88)
    fever = db.session.query(Notification).filter_by(recipient_id=nurse.id, source_id=v["id"], source_table="vital_signs") \
        .join(Notification.alert_rule).filter_by(code="fever").one()
    EK = fever.event_key
    pat_copy = next(n for n in copies(EK) if n.recipient_id == p1.user_id)

    # ---------------- new alerts start as NEW ----------------
    check("new alert: all 3 copies (patient + 2 nurses) status=new", {n.status for n in copies(EK)} == {"new"} and len(copies(EK)) == 3)

    # ---------------- query API ----------------
    b = listing(nt, status="new")
    check("nurse list status=new: one row per event (2 events), own copies, counts",
          len(b["data"]) == 2 and all(n["is_mine"] for n in b["data"]) and b["meta"]["counts"]["new"] == 2
          and b["meta"]["counts"]["open"] == 2, [(n["title"], n["status_text"]) for n in b["data"]])
    check("status=pending (new + acknowledged) → 2 events; meta.counts.pending", len(listing(nt, status="pending")["data"]) == 2
          and listing(nt)["meta"]["counts"]["pending"] == 2)
    check("priority=critical → only fever", [n["alert_rule"]["code"] for n in listing(nt, priority="critical")["data"]] == ["fever"])
    check("patient filter + status=open", len(listing(nt, patient_id=p1.public_id, status="open")["data"]) == 2)
    check("nurse does not see unassigned patient's alerts", all(n["patient"]["patient_code"] == "P00001" for n in listing(nt)["data"]))
    b = listing(at, status="open")
    check("admin sees every patient's events, one per event", sorted(n["patient"]["patient_code"] for n in b["data"]) == ["P00001", "P00001", "P00002"]
          and not any(n["is_mine"] for n in b["data"]))
    check("nurse3 (only P00002 assigned) sees just that event", [n["alert_rule"]["code"] for n in listing(nt3)["data"]] == ["low_spo2"])
    check("bad status → 400", err(c.get("/api/v1/notifications?status=done", headers=H(nt)))[0] == 400)
    check("bad priority → 400", err(c.get("/api/v1/notifications?priority=urgent", headers=H(nt)))[0] == 400)
    pb = listing(pt)
    check("patient list: own copies incl. reminder; status text in patient wording; no internal fields",
          any(n["type"] == "reminder" for n in pb["data"]) and all(n["patient"]["patient_code"] == "P00001" for n in pb["data"])
          and next(n for n in pb["data"] if n["alert_rule"] and n["alert_rule"]["code"] == "fever")["status_text"] == "護理團隊已收到通知"
          and all("acknowledged" not in n for n in pb["data"]))

    # ---------------- detail ----------------
    d = c.get(f"/api/v1/notifications/{fever.id}", headers=H(nt)).get_json()["data"]
    check("detail: patient context (code, diagnosis, cycle day, care alerts)",
          d["patient"]["patient_code"] == "P00001" and d["patient"]["current_cycle"]["cycle_day"] == 4
          and d["patient"]["diagnoses"] and d["patient"]["care_alerts"][0]["description"] == "Penicillin 過敏", d["patient"])
    check("detail: trigger reason (value + condition)", d["trigger"]["value"] == "38.6°C" and d["trigger"]["condition"].startswith("體溫 >= 38")
          and "不在骨髓抑制期" in d["trigger"]["condition"], d["trigger"])
    check("detail: original vital-sign record", d["source_record"]["table"] == "vital_signs" and d["source_record"]["values"]["temperature_c"] == 38.6
          and d["source_record"]["values"]["heart_rate_bpm"] == 126)
    check("detail: recommended action + allowed actions [acknowledge]", "急診" in d["recommended_action"] and d["allowed_actions"] == ["acknowledge"]
          and d["recipients"] == 3)
    check("nurse can open another recipient's copy of an assigned patient (patient copy)",
          c.get(f"/api/v1/notifications/{pat_copy.id}", headers=H(nt)).status_code == 200)
    pd = c.get(f"/api/v1/notifications/{pat_copy.id}", headers=H(pt)).get_json()["data"]
    check("patient detail: reminder + status only — no trigger/source/recommendation/note/names",
          pd["message"].startswith("您的體溫") and pd["status_text"] == "護理團隊已收到通知"
          and not {"trigger", "source_record", "recommended_action", "allowed_actions", "acknowledged"} & set(pd)
          and "resolution_note" not in pd["handling"], sorted(pd))
    check("patient cannot open the nurse's copy → 404", c.get(f"/api/v1/notifications/{fever.id}", headers=H(pt)).status_code == 404)
    check("unassigned nurse → 404", c.get(f"/api/v1/notifications/{fever.id}", headers=H(nt3)).status_code == 404)
    check("admin can open any", c.get(f"/api/v1/notifications/{fever.id}", headers=H(at)).status_code == 200)

    # ---------------- permissions on actions ----------------
    check("patient cannot acknowledge → 403", err(act(pt, pat_copy.id, "acknowledge")) == (403, "FORBIDDEN"))
    check("patient cannot start / resolve → 403", act(pt, pat_copy.id, "start").status_code == 403
          and act(pt, pat_copy.id, "resolve", resolution_note="x").status_code == 403)
    check("unassigned nurse cannot acknowledge → 404", act(nt3, fever.id, "acknowledge").status_code == 404)
    check("nothing changed after refused actions", {n.status for n in copies(EK)} == {"new"})

    # ---------------- invalid transitions ----------------
    r = act(nt, fever.id, "start")
    check("start from new → 409 INVALID_TRANSITION with current status", err(r) == (409, "INVALID_TRANSITION")
          and "待處理" in r.get_json()["error"]["message"])
    check("resolve from new → 409", err(act(nt, fever.id, "resolve", resolution_note="x")) == (409, "INVALID_TRANSITION"))

    # ---------------- lifecycle: acknowledge → start → resolve ----------------
    n_audit = db.session.query(AuditLog).count()
    r = act(nt, fever.id, "acknowledge")
    d = r.get_json()["data"]
    check("acknowledge 200 → acknowledged by nurse1; allowed next = start", r.status_code == 200 and d["status"] == "acknowledged"
          and d["handling"]["acknowledged"]["by"]["display_name"] == "測試護理師 林" and d["allowed_actions"] == ["start"])
    check("all copies acknowledged (patient + both nurses)", {(n.status, n.acknowledged_by) for n in copies(EK)} == {("acknowledged", nurse.id)})
    a = last_audit()
    check("audit: ACKNOWLEDGE, actor nurse1, status new→acknowledged, 3 ids",
          a.action == "ACKNOWLEDGE" and a.actor_user_id == nurse.id and a.changes["status"] == {"old": "new", "new": "acknowledged"}
          and len(a.changes["notification_ids"]) == 3 and a.patient_id == p1.id, a.changes)
    check("acknowledge twice → 409", err(act(nt2, fever.id, "acknowledge")) == (409, "INVALID_TRANSITION"))
    check("resolve from acknowledged → 409", err(act(nt, fever.id, "resolve", resolution_note="x")) == (409, "INVALID_TRANSITION"))
    pd = c.get(f"/api/v1/notifications/{pat_copy.id}", headers=H(pt)).get_json()["data"]
    check("patient now sees 「護理師已接手」 (no nurse name)", pd["status_text"] == "護理師已接手" and "by" not in pd["handling"]["acknowledged"])

    n2_copy = next(n for n in copies(EK) if n.recipient_id == nurse2.id)
    r = act(nt2, n2_copy.id, "start")
    check("another assigned nurse can start → in_progress, started by nurse2", r.status_code == 200
          and r.get_json()["data"]["handling"]["started"]["by"]["display_name"] == "測試護理師 陳")
    a = last_audit()
    check("audit: UPDATE acknowledged→in_progress by nurse2", a.action == "UPDATE" and a.actor_user_id == nurse2.id
          and a.changes["status"] == {"old": "acknowledged", "new": "in_progress"})
    check("resolve without note → 400", err(act(nt, fever.id, "resolve")) == (400, "VALIDATION_ERROR"))
    r = act(nt, fever.id, "resolve", resolution_note="  已電話聯繫，請病人立即至急診  ")
    d = r.get_json()["data"]
    check("resolve 200 → resolved with trimmed internal note; timeline complete", r.status_code == 200 and d["status"] == "resolved"
          and d["handling"]["resolution_note"] == "已電話聯繫，請病人立即至急診" and d["handling"]["resolved"]["by"]["display_name"] == "測試護理師 林"
          and d["allowed_actions"] == [])
    check("all copies resolved", {(n.status, n.resolved_by) for n in copies(EK)} == {("resolved", nurse.id)})
    a = last_audit()
    check("audit: UPDATE in_progress→resolved; note text not stored in audit",
          a.action == "UPDATE" and a.changes["status"] == {"old": "in_progress", "new": "resolved"} and "已電話聯繫" not in str(a.changes))
    check("3 transitions → exactly 3 audit rows", db.session.query(AuditLog).count() - n_audit == 3)
    check("resolve again → 409", err(act(nt, fever.id, "resolve", resolution_note="x")) == (409, "INVALID_TRANSITION"))
    pd = c.get(f"/api/v1/notifications/{pat_copy.id}", headers=H(pt)).get_json()["data"]
    pl = next(n for n in listing(pt)["data"] if n["id"] == pat_copy.id)
    check("patient sees 已處理完成, never the internal note (detail + list)",
          pd["status_text"] == "已處理完成" and "已電話聯繫" not in str(pd) and "已電話聯繫" not in str(pl), pd["handling"])
    b = listing(nt, status="resolved")
    check("staff resolved tab lists it; legacy 'acknowledged' field carries the resolution",
          b["data"][0]["id"] == fever.id and b["data"][0]["acknowledged"]["resolution_note"] == "已電話聯繫，請病人立即至急診")

    # ---------------- non-alert + admin ----------------
    reminder = db.session.query(Notification).filter_by(recipient_id=p1.user_id, type="reminder").first()
    check("reminders have no lifecycle (status null in payload)", next(n for n in listing(pt)["data"] if n["id"] == reminder.id)["status"] is None)
    spo2 = db.session.query(Notification).filter_by(recipient_id=nurse3.id).one()
    check("admin can acknowledge any patient's alert", act(at, spo2.id, "acknowledge").status_code == 200 and last_audit().actor_user_id == admin.id)

    # ---------------- open (acknowledged / in progress) alerts still drive risk ----------------
    tachy = db.session.query(Notification).filter_by(recipient_id=nurse.id, source_id=v["id"]).join(Notification.alert_rule).filter_by(code="tachycardia").one()
    act(nt, tachy.id, "acknowledge")
    act(nt, tachy.id, "start")
    nv = c.get(f"/api/v1/dashboard/patient/{p1.public_id}", headers=H(nt)).get_json()["data"]["widgets"]["nurse-view"]
    check("in_progress alert still counted as unresolved in nurse-view (with status)",
          nv["unacknowledged_alerts"]["count"] == 1 and nv["unacknowledged_alerts"]["items"][0]["status"] == "in_progress"
          and any("心跳過快" in r for r in nv["risk"]["reasons"]), nv["risk"]["reasons"])
    rs = c.get("/api/v1/dashboard/patient/me", headers=H(pt)).get_json()["data"]["widgets"]["risk-summary"]
    item = next(i for i in rs["today"]["alerts"]["items"] if i["title"] == "心跳過快")
    check("patient risk-summary shows status text, no note", item["status_text"] == "護理師正在處理" and "resolution_note" not in item and rs["today"]["alerts"]["open"] == 1)

    # ---------------- quick resolve (existing flows) fills the missing steps ----------------
    r = c.patch(f"/api/v1/notifications/{tachy.id}/resolve", json={"resolution_note": "已衛教休息後複測"}, headers=H(nt))
    t_copies = copies(tachy.event_key)
    check("PATCH quick resolve from in_progress → resolved, keeps earlier steps", r.status_code == 200
          and {n.status for n in t_copies} == {"resolved"} and all(n.acknowledged_by == nurse.id and n.started_at for n in t_copies))
    vitals(pt, spo2_pct=89)
    low = db.session.query(Notification).filter_by(recipient_id=nurse.id).join(Notification.alert_rule).filter_by(code="low_spo2").one()
    c.patch(f"/api/v1/notifications/{low.id}/resolve", json={"resolution_note": "已聯絡"}, headers=H(nt))
    lc = copies(low.event_key)
    check("PATCH quick resolve from new → acknowledged/started/resolved all filled by same nurse",
          all(n.status == "resolved" and n.acknowledged_by == n.started_by == n.resolved_by == nurse.id for n in lc))
    check("PATCH on an already resolved alert → 422 INVALID_STATE (existing contract)",
          err(c.patch(f"/api/v1/notifications/{low.id}/resolve", json={"resolution_note": "x"}, headers=H(nt))) == (422, "INVALID_STATE"))

    # ---------------- symptom review resolves alerts via fast-forward ----------------
    rec = c.post("/api/v1/symptoms/records", json={"patient_id": "me", "form_code": "daily_chemo_check", "values": [
        {"definition_code": "pain", "value_numeric": 9}, {"definition_code": "nausea", "value_numeric": 1},
        {"definition_code": "fatigue", "value_numeric": 1}, {"definition_code": "fever", "value_boolean": False}]},
        headers=H(pt, str(uuid.uuid4()))).get_json()["data"]
    pain = db.session.query(Notification).filter_by(source_table="symptom_records", source_id=rec["id"], recipient_id=nurse.id).one()
    act(nt, pain.id, "acknowledge")
    r = c.post(f"/api/v1/symptoms/records/{rec['id']}/review", json={"assessment_type": "phone_follow_up", "action_note": "已電話評估疼痛，調整止痛藥",
                                                                       "risk_level": "medium", "resolve_alerts": True}, headers=H(nt))
    pc = copies(pain.event_key)
    check("symptom review with resolve_alerts closes an acknowledged alert (steps filled)", r.status_code in (200, 201)
          and all(n.status == "resolved" and n.acknowledged_by == nurse.id and n.started_by == nurse.id for n in pc), r.get_json())
    d = c.get(f"/api/v1/notifications/{pain.id}", headers=H(nt)).get_json()["data"]
    check("detail for a symptom alert: trigger + original symptom values", d["trigger"]["value"] == "9" and d["trigger"]["condition"] == "疼痛 >= 7"
          and any(v["definition_code"] == "pain" and v["score"] == 9 for v in d["source_record"]["values"]), d["trigger"])

    # ---------------- lab alert detail ----------------
    r = c.post("/api/v1/labs/results", json={"patient_id": p1.public_id, "collected_at": v["measured_at"],
                                              "results": [{"test_code": "ANC", "value": 0.8}]}, headers=H(nt))
    lab = db.session.query(Notification).filter_by(source_table="lab_results", recipient_id=nurse.id).one()
    d = c.get(f"/api/v1/notifications/{lab.id}", headers=H(nt)).get_json()["data"]
    check("detail for a lab alert: condition with value_above, lab row with reference range",
          d["trigger"]["condition"] == "嗜中性白血球（抵抗力） <= 1 且 > 0.5" and d["source_record"]["test_code"] == "ANC"
          and d["source_record"]["ref_low"] == 1.5 and "感染" in d["recommended_action"], d["trigger"])

    # ---------------- timestamp precision (same instant) ----------------
    # Every step reads the very same instant: the lifecycle still yields strictly increasing,
    # millisecond-precision times, identical on every copy of the event.
    import re
    from datetime import datetime as _dt
    from unittest.mock import patch

    import app.modules.notification.services as notif_services

    MS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    frozen = _dt(2026, 9, 25, 3, 0, 0, 999000)  # last millisecond of a second
    for rid in (p1.user_id, nurse.id):
        db.session.add(Notification(recipient_id=rid, patient_id=p1.id, event_key="test:frozen", type="risk_alert",
                                    severity="warning", title="同秒", message="x"))
    db.session.commit()
    nid = db.session.query(Notification).filter_by(event_key="test:frozen", recipient_id=nurse.id).one().id
    with patch.object(notif_services, "utcnow", lambda: frozen):
        steps_ok = [c.post(f"/api/v1/notifications/{nid}/{a}", json={"resolution_note": "同秒"} if a == "resolve" else None,
                           headers=H(nt)).status_code for a in ("acknowledge", "start", "resolve")]
    h = c.get(f"/api/v1/notifications/{nid}", headers=H(nt)).get_json()["data"]["handling"]
    times = [h[k]["at"] for k in ("acknowledged", "started", "resolved")]
    check("same-instant acknowledge → start → resolve: ms format, strictly increasing (1 ms apart, may cross the second)",
          steps_ok == [200, 200, 200] and all(MS.match(t) for t in times) and times == sorted(set(times)), times)
    rows = db.session.query(Notification).filter_by(event_key="test:frozen").all()
    check("every copy stores the same increasing step times",
          len({(r.acknowledged_at, r.started_at, r.resolved_at) for r in rows}) == 1 and rows[0].acknowledged_at < rows[0].started_at < rows[0].resolved_at)
    listed = c.get("/api/v1/notifications?status=all&per_page=100", headers=H(nt)).get_json()["data"]
    check("every notification timestamp in API responses uses the ms format",
          all(MS.match(n["created_at"]) for n in listed)
          and all(MS.match(v["at"]) for n in listed for v in (n["handling"] or {}).values() if isinstance(v, dict)))

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
