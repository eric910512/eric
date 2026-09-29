"""Sprint 8: manual and scheduled reminders — create (nurse for assigned patients, admin), validation,
idempotency, origin (manual / scheduled / alert_rule), visibility only when due, the same handling
lifecycle as risk alerts, patient privacy, permissions and audit."""
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
from app.models import AuditLog, Notification, PatientProfile
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


def iso(dt):
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()
    nt, pt, at = token("nurse01@demo.local"), token("patient01@demo.local"), token("admin01@demo.local")
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    pid = p1.public_id
    url = "/api/v1/notifications"

    # ================================================================ create: now
    body = {"patient_id": pid, "title": "回診前請空腹", "message": "明天 08:30 報到，05:00 起請空腹。", "severity": "info"}
    key = str(uuid.uuid4())
    r = c.post(url, json=body, headers=H(nt, key))
    d = r.get_json()["data"]
    check("nurse creates a reminder for an assigned patient → 201", r.status_code == 201 and d["type"] == "reminder", r.status_code)
    check("sent now → origin manual, status new, no scheduled_for", d["origin"] == "manual" and d["status"] == "new" and d["scheduled_for"] is None, d)
    check("staff payload has the lifecycle (status text, handling)", d["status_text"] == "待處理" and d["handling"]["acknowledged"] is None)
    r2 = c.post(url, json=body, headers=H(nt, key))
    check("same Idempotency-Key → same reminder, not a second one", r2.status_code == 201 and r2.get_json()["data"]["id"] == d["id"]
          and db.session.query(Notification).filter_by(title="回診前請空腹").count() == 1)
    check("same key with a different body → 422", c.post(url, json={**body, "title": "別的"}, headers=H(nt, key)).status_code == 422)
    now_id = d["id"]

    # patient sees it at once, without the lifecycle or staff details
    lst = c.get(f"{url}?per_page=100", headers=H(pt)).get_json()["data"]
    mine = next((n for n in lst if n["id"] == now_id), None)
    check("patient sees the reminder right away", mine is not None and mine["is_read"] is False)
    check("patient copy: no lifecycle / staff fields", mine and mine["status"] is None and mine["handling"] is None and "acknowledged" not in mine, mine)
    check("unread count includes it", c.get(f"{url}/unread-count", headers=H(pt)).get_json()["data"]["unread"] >= 1)

    # ================================================================ create: scheduled
    when = utcnow() + timedelta(hours=20)
    r = c.post(url, json={"patient_id": pid, "title": "明天化療提醒", "message": "記得帶健保卡。", "severity": "warning", "scheduled_for": iso(when)}, headers=H(nt))
    s = r.get_json()["data"]
    check("scheduled reminder → 201, origin scheduled, scheduled_for echoed", r.status_code == 201 and s["origin"] == "scheduled" and s["scheduled_for"].startswith(when.isoformat()[:16]), s.get("scheduled_for"))
    lst = c.get(f"{url}?per_page=100", headers=H(pt)).get_json()["data"]
    check("patient does not see it before its time", all(n["id"] != s["id"] for n in lst))
    check("patient cannot open it before its time → 404", c.get(f"{url}/{s['id']}", headers=H(pt)).status_code == 404
          and c.patch(f"{url}/{s['id']}/read", headers=H(pt)).status_code == 404)
    unread_before = c.get(f"{url}/unread-count", headers=H(pt)).get_json()["data"]["unread"]
    c.post(f"{url}/read-all", headers=H(pt))
    check("read-all does not touch it", db.session.get(Notification, s["id"]).is_read is not True)
    sch = c.get(f"{url}/scheduled?patient_id={pid}", headers=H(nt))
    check("staff list of not-yet-due reminders holds it", sch.status_code == 200 and [x["id"] for x in sch.get_json()["data"]] == [s["id"]], sch.get_json())
    check("staff reminder list (type=reminder) hides it until due", all(n["id"] != s["id"] for n in c.get(f"{url}?type=reminder&patient_id={pid}", headers=H(nt)).get_json()["data"]))
    check("patient cannot list scheduled reminders → 403", c.get(f"{url}/scheduled?patient_id=me", headers=H(pt)).status_code == 403)

    # time passes: the reminder becomes due
    db.session.get(Notification, s["id"]).scheduled_for = utcnow() - timedelta(minutes=1)
    db.session.commit()
    lst = c.get(f"{url}?per_page=100", headers=H(pt)).get_json()["data"]
    check("once due, the patient sees it (unread)", any(n["id"] == s["id"] and n["is_read"] is False for n in lst))
    check("unread count goes up by one", c.get(f"{url}/unread-count", headers=H(pt)).get_json()["data"]["unread"] == 1, unread_before)
    check("no longer in the not-yet-due list", c.get(f"{url}/scheduled?patient_id={pid}", headers=H(nt)).get_json()["data"] == [])

    # ================================================================ lifecycle (same as risk alerts)
    rid = now_id
    r = c.post(f"{url}/{rid}/start", headers=H(nt))
    check("start before acknowledge → 409 INVALID_TRANSITION", r.status_code == 409 and r.get_json()["error"]["code"] == "INVALID_TRANSITION")
    r = c.post(f"{url}/{rid}/acknowledge", headers=H(nt))
    check("acknowledge → acknowledged", r.status_code == 200 and r.get_json()["data"]["status"] == "acknowledged")
    check("detail offers the next step", r.get_json()["data"]["allowed_actions"] == ["start"])
    check("start → in_progress", c.post(f"{url}/{rid}/start", headers=H(nt)).get_json()["data"]["status"] == "in_progress")
    check("resolve without note → 400", c.post(f"{url}/{rid}/resolve", json={}, headers=H(nt)).status_code == 400)
    r = c.post(f"{url}/{rid}/resolve", json={"resolution_note": "病人回覆已了解（內部）"}, headers=H(nt))
    check("resolve → resolved", r.status_code == 200 and r.get_json()["data"]["status"] == "resolved")
    check("resolve again → 409", c.post(f"{url}/{rid}/resolve", json={"resolution_note": "x"}, headers=H(nt)).status_code == 409)
    pd = c.get(f"{url}/{rid}", headers=H(pt)).get_json()["data"]
    check("patient detail after handling: no note, no nurse name", "病人回覆已了解" not in json.dumps(pd, ensure_ascii=False)
          and "測試護理師" not in json.dumps(pd, ensure_ascii=False) and pd["status"] is None)
    check("patient cannot move the lifecycle → 403", c.post(f"{url}/{s['id']}/acknowledge", headers=H(pt)).status_code == 403)
    lst = c.get(f"{url}?type=reminder&patient_id={pid}&status=resolved", headers=H(nt)).get_json()
    check("staff filter type=reminder&status=resolved", [n["id"] for n in lst["data"]] == [rid] and lst["meta"]["counts"]["resolved"] == 1, lst["meta"]["counts"])
    alerts = c.get(f"{url}?status=open", headers=H(nt)).get_json()["data"]
    check("the risk-alert list (default) is unchanged: no reminders", all(n["type"] == "risk_alert" for n in alerts))

    # origin of other notifications
    c.post("/api/v1/vital-signs", json={"patient_id": "me", "temperature_c": 38.9}, headers=H(pt))
    risk = c.get(f"{url}?per_page=5", headers=H(nt)).get_json()["data"][0]
    seed = next(n for n in c.get(f"{url}?per_page=100", headers=H(pt)).get_json()["data"] if n["title"] == "今日回診提醒")
    check("origin: risk alert → alert_rule; appointment reminder (seed) → scheduled", risk["origin"] == "alert_rule" and seed["origin"] == "scheduled", (risk["origin"], seed["origin"]))

    # ================================================================ validation / permissions
    bad = c.post(url, json={"patient_id": pid, "title": "", "message": "x" * 2001, "severity": "critical", "scheduled_for": "tomorrow", "extra": 1}, headers=H(nt))
    fields = {e["field"] for e in bad.get_json()["error"]["details"]}
    check("validation: title, message, severity (no critical), scheduled_for, unknown field → 400",
          bad.status_code == 400 and {"title", "message", "severity", "scheduled_for", "extra"} <= fields, fields)
    past = c.post(url, json={**body, "scheduled_for": iso(utcnow() - timedelta(hours=1))}, headers=H(nt))
    far = c.post(url, json={**body, "scheduled_for": iso(utcnow() + timedelta(days=400))}, headers=H(nt))
    check("scheduled_for in the past or > 1 year → 400", past.status_code == 400 and far.status_code == 400)
    check("missing patient_id → 400", c.post(url, json={"title": "a", "message": "b"}, headers=H(nt)).status_code == 400)
    check("patient cannot create reminders → 403", c.post(url, json={**body, "patient_id": "me"}, headers=H(pt)).status_code == 403)
    np_ = c.post("/api/v1/patients", json={"display_name": "測試病人 提醒", "gender": "female", "date_of_birth": "1970-01-01"}, headers=H(at)).get_json()["data"]
    r = c.post(url, json={**body, "patient_id": np_["id"]}, headers=H(nt))
    check("nurse, unassigned patient → 404 (existence not revealed)", r.status_code == 404)
    check("nurse, scheduled list of unassigned patient → 404", c.get(f"{url}/scheduled?patient_id={np_['id']}", headers=H(nt)).status_code == 404)
    r = c.post(url, json={**body, "patient_id": np_["id"]}, headers=H(at))
    check("patient without an account → 422 NO_PATIENT_ACCOUNT", r.status_code == 422 and r.get_json()["error"]["code"] == "NO_PATIENT_ACCOUNT")
    r = c.post(url, json={**body, "title": "管理者提醒"}, headers=H(at))
    check("admin can send a reminder", r.status_code == 201 and r.get_json()["data"]["origin"] == "manual")
    check("unknown patient id → 404", c.post(url, json={**body, "patient_id": str(uuid.uuid4())}, headers=H(at)).status_code == 404)

    # ending the assignment removes access
    asg = c.get(f"/api/v1/patients/{pid}/nurse-assignments", headers=H(at)).get_json()["data"]
    active = [a for a in asg if a.get("ended_at") is None]
    for a in active:
        c.post(f"/api/v1/patients/{pid}/nurse-assignments/{a['id']}/end", json={"reason": "E2E 測試結束"}, headers=H(at))
    check("after the assignment ends: nurse cannot create, list or handle → 404",
          c.post(url, json=body, headers=H(nt)).status_code == 404
          and c.get(f"{url}/scheduled?patient_id={pid}", headers=H(nt)).status_code == 404
          and c.post(f"{url}/{s['id']}/acknowledge", headers=H(nt)).status_code == 404, [a["id"] for a in active])

    # ================================================================ audit
    logs = db.session.query(AuditLog).filter_by(resource_type="notifications", action="CREATE").all()
    check("every reminder creation audited (origin, scheduled_for, actor)", len(logs) == 3 and {l.changes["origin"] for l in logs} == {"manual", "scheduled"}
          and all(l.actor_user_id for l in logs), len(logs))
    steps = db.session.query(AuditLog).filter(AuditLog.resource_type == "notifications", AuditLog.resource_id == str(rid)).all()
    check("lifecycle steps audited", {l.action for l in steps} >= {"ACKNOWLEDGE", "UPDATE"} and len(steps) >= 3, [l.action for l in steps])
    check("audit never contains passwords", "Demo@1234" not in json.dumps([l.changes for l in db.session.query(AuditLog).all()], ensure_ascii=False, default=str))

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
