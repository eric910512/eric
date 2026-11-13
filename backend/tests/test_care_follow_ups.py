"""護理師追蹤管理 (Phase 1c): create (with the review or afterwards), list / filter / sort / page, detail and
history, reschedule (optimistic lock, history keeps every date), complete (a new signed nursing assessment,
the review untouched, idempotent, chain to the next follow-up), cancel; the patient's read-only view; access
(unassigned nurse, ended assignment, admin, other patient — nothing revealed); state rules (no double close,
no reopening); races (complete vs cancel, two reschedules, two creates); rollback when the completion record
fails; time (UTC stored, Taipei dates, overdue computed); audit; the migration (protected downgrade) — on
throw-away databases only."""
import json
import os
import sys
import tempfile
import uuid
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from sqlalchemy import text as sql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.dml import Update
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import (AuditLog, CareFollowUp, CareFollowUpEvent, NursePatientAssignment, NursingAssessment, PatientProfile, Role,
                        SymptomRecord, User)
from app.models.base import utcnow
from app.modules.follow_up import services as fs
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()
R = "/api/v1/symptoms/records"
F = "/api/v1/care-follow-ups"
TPE = timezone(timedelta(hours=8))


def token(email):
    return c.post("/api/v1/auth/login", json={"email": email, "password": "Demo@1234"}).get_json()["data"]["access_token"]


def H(t, key=None):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": key or str(uuid.uuid4())}


def tpe(days=1, hour=10, minute=0):
    d = datetime.now(TPE).date() + timedelta(days=days)
    return f"{d.isoformat()}T{hour:02d}:{minute:02d}:00+08:00"


def code(r):
    return (r.get_json(silent=True) or {}).get("error", {}).get("code")


def skin_report(t, **answers):
    answers = answers or {"skin_itch": "yes"}
    r = c.post(R, json={"patient_id": "me", "form_code": "rt_skin_self_assessment", "values": [
        {"definition_code": k, "option_code": v} for k, v in answers.items()]}, headers=H(t))
    return r.get_json()["data"]["id"]


def review(t, rid, **extra):
    return c.post(f"{R}/{rid}/review", json={"action_note": "已電話衛教皮膚照護", **extra}, headers=H(t))


def create(t, rid, due=None, reason="觀察脫皮範圍", key=None):
    body = {"symptom_record_id": rid, "due_at": due or tpe(2), "reason": reason}
    r = c.post(F, json=body, headers=H(t, key))
    return r, (r.get_json(silent=True) or {})


def act(t, fid, action, key=None, method="post", **body):
    call = c.patch if method == "patch" else c.post
    r = call(f"{F}/{fid}/{action}", json=body, headers=H(t, key))
    return r, (r.get_json(silent=True) or {})


def racing(on_update, table="care_follow_ups"):
    """Run ``on_update`` right before the first UPDATE of ``table`` — another nurse winning the race."""
    original = db.session.execute
    state = {"done": False}

    def execute(stmt, *a, **k):
        if not state["done"] and isinstance(stmt, Update) and stmt.table.name == table:
            state["done"] = True
            on_update(original)
        return original(stmt, *a, **k)
    db.session.execute = execute
    return lambda: db.session.__dict__.pop("execute", None)


with app.app_context():
    db.create_all()
    seed_dev_data()
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    nurse2 = User(role=roles["nurse"], email="nurse02@demo.local", password_hash=pw, display_name="測試護理師 陳", password_changed_at=utcnow())
    u2 = User(role=roles["patient"], email="p2@demo.local", password_hash=pw, display_name="乙", password_changed_at=utcnow())
    p2 = PatientProfile(patient_code="P09002", display_name="測試病人 乙", date_of_birth=date(1960, 1, 1), user=u2, timezone="Asia/Taipei")
    db.session.add_all([nurse2, p2])
    db.session.flush()
    db.session.add(NursePatientAssignment(nurse_id=nurse2.id, patient_id=p2.id))
    db.session.commit()
    pt, p2t, nt, nt2, at = (token(e) for e in ("patient01@demo.local", "p2@demo.local", "nurse01@demo.local", "nurse02@demo.local", "admin01@demo.local"))
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    pid = p1.public_id

    # ================================================================ A. create with the review (one transaction)
    rid1 = skin_report(pt, skin_itch="yes", skin_broken_exudate="yes")
    r = review(nt, rid1, follow_up={"due_at": tpe(2)})
    rec1 = db.session.get(SymptomRecord, rid1)
    check("review with follow_up but no reason → 400, the review is not applied", r.status_code == 400 and rec1.review_status == "submitted"
          and db.session.query(CareFollowUp).count() == 0)
    r = review(nt, rid1, follow_up={"due_at": tpe(2, 9), "reason": "觀察破皮是否改善"})
    body = r.get_json()
    db.session.expire_all()
    fu1 = db.session.query(CareFollowUp).filter_by(symptom_record_id=rid1).one()
    check("review + follow_up → reviewed and one open follow-up, planned by the review's signed assessment", r.status_code == 200
          and fu1.status == "open" and fu1.version == 1 and fu1.review_assessment_id == db.session.get(SymptomRecord, rid1).nursing_assessment_id
          and fu1.reason == "觀察破皮是否改善" and fu1.created_by == db.session.query(User).filter_by(email="nurse01@demo.local").one().id)
    check("review response carries the follow-up summary (nurse)", body["data"]["follow_up"]["id"] == fu1.public_id and body["data"]["follow_up"]["status"] == "open")
    check("created event + audit CREATE care_follow_ups", db.session.query(CareFollowUpEvent).filter_by(follow_up_id=fu1.id, event_type="created").count() == 1
          and db.session.query(AuditLog).filter_by(resource_type="care_follow_ups", action="CREATE", resource_id=fu1.public_id).count() == 1)
    fl = c.get(f"{R}/{pid}?review_status=follow_up", headers=H(nt)).get_json()
    check("records list review_status=follow_up = records with an open follow-up", [x["id"] for x in fl["data"]] == [rid1] and fl["meta"]["follow_up"] == 1)

    # Phase 1a parameter still works: reason = the action note
    rid_legacy = skin_report(pt)
    r = review(nt, rid_legacy, next_follow_up_at=tpe(3))
    legacy = db.session.query(CareFollowUp).filter_by(symptom_record_id=rid_legacy).one()
    check("Phase 1a next_follow_up_at → also a care follow-up (reason = the action note)", r.status_code == 200 and legacy.reason == "已電話衛教皮膚照護")

    # ================================================================ A. create afterwards
    rid2 = skin_report(pt, skin_itch="no")
    r, b = create(nt, rid2)
    check("record not reviewed yet → 409 RECORD_NOT_REVIEWED", r.status_code == 409 and code(r) == "RECORD_NOT_REVIEWED")
    review(nt, rid2)
    for label, kw, field in (("in the past", {"due": tpe(-1)}, "due_at"), ("more than 180 days ahead", {"due": tpe(200)}, "due_at"),
                             ("without a timezone", {"due": "2026-12-01T09:00:00"}, "due_at"), ("without a reason", {"reason": "  "}, "reason")):
        r, b = create(nt, rid2, **kw)
        check(f"create with a due date {label}" if field == "due_at" else "create without a reason", r.status_code == 400 and field in json.dumps(b), b)
    r = c.post(F, json={"symptom_record_id": rid2, "due_at": tpe(2), "reason": "x"}, headers={"Authorization": f"Bearer {nt}"})
    check("no Idempotency-Key → 428", r.status_code == 428)
    key = str(uuid.uuid4())
    due2 = tpe(1, 0, 30)  # 00:30 Taipei = 16:30 UTC the day before
    r, b = create(nt, rid2, due=due2, key=key)
    fu2 = b.get("data", {})
    check("create for a reviewed record → 201 open", r.status_code == 201 and fu2["status"] == "open" and fu2["version"] == 1, b)
    utc_expected = datetime.fromisoformat(due2).astimezone(timezone.utc)
    check("stored in UTC (…Z); Taipei date kept for display across the date line",
          fu2["due_at"] == utc_expected.strftime("%Y-%m-%dT%H:%M:%S.000Z") and fu2["due_local_date"] == due2[:10] and fu2["due_at"][:10] != due2[:10], fu2["due_at"])
    r, b = create(nt, rid2, due=due2, key=key)
    check("same Idempotency-Key → the same follow-up, replayed", r.status_code == 201 and b["data"]["id"] == fu2["id"] and r.headers.get("Idempotent-Replayed") == "true"
          and db.session.query(CareFollowUp).filter_by(symptom_record_id=rid2).count() == 1)
    r, b = create(nt, rid2)
    check("second open follow-up for the same record → 409 FOLLOW_UP_EXISTS (with its id)", r.status_code == 409 and code(r) == "FOLLOW_UP_EXISTS"
          and b["error"]["details"][0]["follow_up_id"] == fu2["id"])
    check("unknown record → 404", create(nt, 999_999)[0].status_code == 404)

    # race: two creates at the same moment — the partial unique index decides
    rid3 = skin_report(pt)
    review(nt, rid3)
    real_select = fs.select
    fs.select = lambda *a: real_select(*a).where(sql("1 = 0"))  # the other request's pre-check saw nothing either
    db.session.add(CareFollowUp(patient_id=p1.id, symptom_record_id=rid3, review_assessment_id=db.session.get(SymptomRecord, rid3).nursing_assessment_id,
                                due_at=utcnow() + timedelta(days=1), reason="另一位護理師同時建立", status="open", version=1,
                                created_by=db.session.query(User).filter_by(email="nurse01@demo.local").one().id))
    db.session.commit()
    r, b = create(nt, rid3)
    fs.select = real_select
    check("race: two creates → the database index allows one open follow-up, the other gets 409", r.status_code == 409 and code(r) == "FOLLOW_UP_EXISTS"
          and db.session.query(CareFollowUp).filter_by(symptom_record_id=rid3, status="open").count() == 1)
    try:
        db.session.execute(sql("INSERT INTO care_follow_ups (public_id, patient_id, symptom_record_id, review_assessment_id, due_at, reason, status,"
                               " version, created_by, created_at, updated_at) SELECT :p, patient_id, symptom_record_id, review_assessment_id,"
                               " due_at, reason, 'open', 1, created_by, created_at, updated_at FROM care_follow_ups WHERE symptom_record_id = :r"),
                           {"p": str(uuid.uuid4()), "r": rid3})
        db.session.commit()
        index_ok = False
    except IntegrityError:
        db.session.rollback()
        index_ok = True
    check("database: a second open row for one record is refused (partial unique index)", index_ok)

    # ================================================================ list / filters / detail / history
    lst = c.get(F, headers=H(nt)).get_json()
    ids = [x["id"] for x in lst["data"]]
    item = next(x for x in lst["data"] if x["id"] == fu2["id"])
    check("nurse list (open, by due date): patient code, report date, reason, due, status, creator, updated", {fu1.public_id, fu2["id"]} <= set(ids)
          and item["patient"]["patient_code"] == "P00001" and item["symptom_record"]["id"] == rid2 and item["reason"] == "觀察脫皮範圍"
          and item["created_by"]["display_name"] and item["updated_at"] and item["overdue"] is False)
    dues = [x["due_at"] for x in lst["data"]]
    check("sorted by due date (earliest first); -due_at reverses", dues == sorted(dues)
          and [x["due_at"] for x in c.get(f"{F}?sort=-due_at", headers=H(nt)).get_json()["data"]] == sorted(dues, reverse=True))
    page2 = c.get(f"{F}?per_page=1&page=2", headers=H(nt)).get_json()
    check("pagination (per_page / page / total)", len(page2["data"]) == 1 and page2["meta"]["total"] == len(ids) and page2["data"][0]["id"] == ids[1])
    check("patient filter", all(x["patient"]["id"] == pid for x in c.get(f"{F}?patient_id={pid}", headers=H(nt)).get_json()["data"]))
    check("unknown status filter → 400", c.get(f"{F}?status=later", headers=H(nt)).status_code == 400)
    r = c.get(f"{F}/{fu2['id']}", headers=H(nt))
    check("detail → 200, audited (VIEW care_follow_ups)", r.status_code == 200 and db.session.query(AuditLog).filter_by(
        resource_type="care_follow_ups", action="VIEW", resource_id=fu2["id"]).count() == 1)

    # ================================================================ reschedule (optimistic lock, history)
    old_due = fu2["due_at"]
    rkey = str(uuid.uuid4())
    r, b = act(nt, fu2["id"], "reschedule", key=rkey, method="patch", due_at=tpe(4, 14), reason="病人改約時間", version=1)
    check("reschedule (version 1) → 200, version 2, new date", r.status_code == 200 and b["data"]["version"] == 2 and b["data"]["due_at"] != old_due, b)
    r, b = act(nt, fu2["id"], "reschedule", key=rkey, method="patch", due_at=tpe(4, 14), reason="病人改約時間", version=1)
    check("same Idempotency-Key → replayed, no second change", r.status_code == 200 and r.headers.get("Idempotent-Replayed") == "true"
          and b["data"]["version"] == 2)
    r, b = act(nt, fu2["id"], "reschedule", method="patch", due_at=tpe(5), reason="另一位護理師以舊畫面改期", version=1)
    check("stale version → 409 VERSION_CONFLICT (no lost update)", r.status_code == 409 and code(r) == "VERSION_CONFLICT"
          and db.session.query(CareFollowUp).filter_by(public_id=fu2["id"]).one().version == 2)
    r, _ = act(nt, fu2["id"], "reschedule", method="patch", due_at=tpe(5), version=2)
    r2, _ = act(nt, fu2["id"], "reschedule", method="patch", due_at=tpe(-2), reason="x", version=2)
    check("reschedule needs a reason and a valid date", r.status_code == 400 and r2.status_code == 400)

    fu2_row = db.session.query(CareFollowUp).filter_by(public_id=fu2["id"]).one()
    due_before_race = fu2_row.due_at
    restore = racing(lambda run: run(sql("UPDATE care_follow_ups SET version = version + 1 WHERE id = :i"), {"i": fu2_row.id}))
    r, _ = act(nt, fu2["id"], "reschedule", method="patch", due_at=tpe(6), reason="同時改期", version=2)
    restore()
    db.session.rollback()
    db.session.expire_all()
    check("race: two reschedules at once → the second gets 409 VERSION_CONFLICT, the date is not overwritten", r.status_code == 409
          and code(r) == "VERSION_CONFLICT" and db.session.query(CareFollowUp).filter_by(public_id=fu2["id"]).one().due_at == due_before_race)
    ev = c.get(f"{F}/{fu2['id']}/events", headers=H(nt)).get_json()["data"]
    check("history: created, then rescheduled with the old and the new date and the reason; nothing lost",
          [e["event_type"] for e in ev] == ["created", "rescheduled"] and ev[1]["old_due_at"] == old_due and ev[1]["reason"] == "病人改約時間"
          and ev[1]["actor"]["display_name"])
    check("history view audited (VIEW care_follow_up_events)", db.session.query(AuditLog).filter_by(resource_type="care_follow_up_events", action="VIEW").count() >= 1)

    # ================================================================ complete (signed nursing assessment, review untouched)
    review_a = db.session.get(NursingAssessment, db.session.get(SymptomRecord, rid2).nursing_assessment_id)
    review_before = (review_a.subjective, review_a.assessment, review_a.plan, review_a.sign_status, review_a.next_follow_up_at, review_a.updated_at)
    values_before = sorted((v.definition_id, v.option_id) for v in db.session.get(SymptomRecord, rid2).values)
    r, b = act(nt, fu2["id"], "complete", version=2, result="   ")
    check("complete without a result / actions → 400", r.status_code == 400 and "result" in json.dumps(b) and "actions" in json.dumps(b))
    real_na = fs.NursingAssessment
    fs.NursingAssessment = lambda **kw: (_ for _ in ()).throw(RuntimeError("simulated signing failure"))
    try:
        failed = act(nt, fu2["id"], "complete", version=2, result="皮膚完整", actions="衛教保濕")[0].status_code == 500
    except RuntimeError:
        failed = True
    fs.NursingAssessment = real_na
    db.session.rollback()
    row = db.session.query(CareFollowUp).filter_by(public_id=fu2["id"]).one()
    check("the completion record cannot be written → the follow-up stays open (version 2), no completed event", failed and row.status == "open"
          and row.version == 2 and db.session.query(CareFollowUpEvent).filter_by(follow_up_id=row.id, event_type="completed").count() == 0)
    na_before = db.session.query(NursingAssessment).count()
    ckey = str(uuid.uuid4())
    r, b = act(nt, fu2["id"], "complete", key=ckey, version=2, result="脫皮範圍縮小，無滲液", actions="持續保濕、避免摩擦", assessment_type="phone_follow_up")
    db.session.expire_all()
    row = db.session.query(CareFollowUp).filter_by(public_id=fu2["id"]).one()
    comp = row.completion_assessment
    check("complete → completed, by whom and when, with a NEW signed nursing assessment (result / actions)", r.status_code == 200
          and row.status == "completed" and row.completed_by and row.completed_at and comp is not None and comp.sign_status == "signed"
          and comp.assessment == "脫皮範圍縮小，無滲液" and comp.plan == "持續保濕、避免摩擦" and "觀察脫皮範圍" in comp.subjective
          and comp.id != row.review_assessment_id and b["data"]["completion"]["result"] == "脫皮範圍縮小，無滲液")
    review_a = db.session.get(NursingAssessment, row.review_assessment_id)
    check("the review's signed assessment and the patient's answers are unchanged",
          (review_a.subjective, review_a.assessment, review_a.plan, review_a.sign_status, review_a.next_follow_up_at, review_a.updated_at) == review_before
          and sorted((v.definition_id, v.option_id) for v in db.session.get(SymptomRecord, rid2).values) == values_before)
    r, b = act(nt, fu2["id"], "complete", key=ckey, version=2, result="脫皮範圍縮小，無滲液", actions="持續保濕、避免摩擦", assessment_type="phone_follow_up")
    check("same Idempotency-Key → replayed; exactly one completion assessment", r.status_code == 200 and r.headers.get("Idempotent-Replayed") == "true"
          and db.session.query(NursingAssessment).count() == na_before + 1)
    for action, kw in (("complete", {"result": "x", "actions": "y"}), ("cancel", {"reason": "x"}), ("reschedule", {"due_at": tpe(3), "reason": "x"})):
        r, _ = act(nt, fu2["id"], action, method="patch" if action == "reschedule" else "post", version=3, **kw)
        check(f"completed → {action} refused (409 FOLLOW_UP_CLOSED; never reopened)", r.status_code == 409 and code(r) == "FOLLOW_UP_CLOSED")
    check("audit: CREATE nursing_assessments (follow-up) + UPDATE care_follow_ups (completed)",
          db.session.query(AuditLog).filter_by(resource_type="nursing_assessments", action="CREATE").filter(AuditLog.changes.isnot(None)).count() >= 1
          and db.session.query(AuditLog).filter_by(resource_type="care_follow_ups", action="UPDATE", resource_id=fu2["id"]).count() >= 2)

    # chain: complete fu1 with the next follow-up
    r, b = act(nt, fu1.public_id, "complete", version=1, result="破皮處仍有滲液", actions="已衛教換藥方式", assessment_type="follow_up",
               next_follow_up={"due_at": tpe(7, 10), "reason": "一週後再確認滲液"})
    nxt = b.get("data", {}).get("next_follow_up")
    db.session.expire_all()
    n_row = db.session.query(CareFollowUp).filter_by(public_id=nxt["id"]).one() if nxt else None
    check("complete + next follow-up → a new open follow-up linked to the previous one (same record)", r.status_code == 200 and nxt
          and n_row.previous_follow_up_id == db.session.query(CareFollowUp).filter_by(public_id=fu1.public_id).one().id
          and n_row.symptom_record_id == rid1 and n_row.status == "open" and b["data"]["next_follow_up_ids"] == [nxt["id"]]
          and n_row.review_assessment_id == db.session.query(CareFollowUp).filter_by(public_id=fu1.public_id).one().completion_assessment_id, b)
    ev = c.get(f"{F}/{nxt['id']}/events", headers=H(nt)).get_json()["data"]
    check("the next one's history starts with created, pointing to the previous follow-up", ev[0]["event_type"] == "created"
          and ev[0]["related_follow_up_id"] == fu1.public_id)

    # ================================================================ cancel
    r, b = act(nt, nxt["id"], "cancel", version=1, reason=" ")
    check("cancel without a reason → 400", r.status_code == 400)
    restore = racing(lambda run: run(sql("UPDATE care_follow_ups SET status = 'completed', completed_by = created_by, completed_at = :n,"
                                         " completion_assessment_id = review_assessment_id, version = version + 1 WHERE public_id = :p"),
                                     {"n": utcnow(), "p": nxt["id"]}))
    r, _ = act(nt, nxt["id"], "cancel", version=1, reason="同時結案")
    restore()
    db.session.rollback()
    check("race: cancel while another nurse completes → 409 FOLLOW_UP_CLOSED, nothing of the cancel written", r.status_code == 409
          and code(r) == "FOLLOW_UP_CLOSED" and db.session.query(CareFollowUp).filter_by(public_id=nxt["id"]).one().cancelled_at is None)
    na_before = db.session.query(NursingAssessment).count()
    restore = racing(lambda run: run(sql("UPDATE care_follow_ups SET status = 'cancelled', cancelled_by = created_by, cancelled_at = :n,"
                                         " cancellation_reason = '同時取消', version = version + 1 WHERE public_id = :p"),
                                     {"n": utcnow(), "p": nxt["id"]}))
    r, _ = act(nt, nxt["id"], "complete", version=1, result="x", actions="y")
    restore()
    db.session.rollback()
    check("race: complete while another nurse cancels → 409, the completion assessment rolled back", r.status_code == 409
          and db.session.query(NursingAssessment).count() == na_before and db.session.query(CareFollowUp).filter_by(public_id=nxt["id"]).one().status == "open")
    xkey = str(uuid.uuid4())
    r, b = act(nt, nxt["id"], "cancel", key=xkey, version=1, reason="病人轉院")
    db.session.expire_all()
    x_row = db.session.query(CareFollowUp).filter_by(public_id=nxt["id"]).one()
    check("cancel → cancelled, by whom, when, why; the follow-up and its record are kept", r.status_code == 200 and x_row.status == "cancelled"
          and x_row.cancelled_by and x_row.cancelled_at and x_row.cancellation_reason == "病人轉院" and db.session.get(SymptomRecord, rid1) is not None)
    r, _ = act(nt, nxt["id"], "cancel", key=xkey, version=1, reason="病人轉院")
    r2, _ = act(nt, nxt["id"], "cancel", version=2, reason="再取消一次")
    r3, _ = act(nt, nxt["id"], "complete", version=2, result="x", actions="y")
    check("cancel replay → 200 replayed; cancel again / complete a cancelled one → 409", r.status_code == 200 and r.headers.get("Idempotent-Replayed") == "true"
          and r2.status_code == 409 and r3.status_code == 409 and code(r3) == "FOLLOW_UP_CLOSED")
    def refused(change):
        event_row = db.session.query(CareFollowUpEvent).first()
        try:
            change(event_row)
            db.session.flush()
            return False
        except RuntimeError:
            return True
        finally:
            db.session.rollback()

    check("events are append-only (update / delete refused)", refused(lambda e: setattr(e, "reason", "改寫")) and refused(db.session.delete))

    # ================================================================ overdue, same day, filters
    rid4 = skin_report(pt)
    review(nt, rid4, follow_up={"due_at": tpe(0, 23, 59), "reason": "同一天第一筆"})
    rid5 = skin_report(pt)
    review(nt, rid5, follow_up={"due_at": tpe(0, 23, 58), "reason": "同一天第二筆"})
    f4 = db.session.query(CareFollowUp).filter_by(symptom_record_id=rid4).one()
    f4.due_at = utcnow() - timedelta(hours=2)  # due date passed
    db.session.commit()
    od = c.get(f"{F}?status=overdue", headers=H(nt)).get_json()
    check("overdue = open and due in the past (computed, not stored)", [x["id"] for x in od["data"]] == [f4.public_id] and od["data"][0]["overdue"] is True
          and f4.status == "open" and od["meta"]["counts"]["overdue"] == 1)
    op = c.get(f"{F}?status=open", headers=H(nt)).get_json()
    check("open includes the overdue one (earliest first) and same-day follow-ups stay apart", op["data"][0]["id"] == f4.public_id
          and sum(1 for x in op["data"] if x["reason"].startswith("同一天")) == 2)
    counts = op["meta"]["counts"]
    check("counts per status (open / overdue / completed / cancelled / all)", counts["completed"] == 2 and counts["cancelled"] == 1
          and counts["all"] == counts["open"] + counts["completed"] + counts["cancelled"])

    # ================================================================ patient view (read only, minimal)
    mine = c.get("/api/v1/patients/me/care-follow-ups", headers=H(pt)).get_json()
    blob = json.dumps(mine, ensure_ascii=False)
    check("patient: own follow-ups with state and planned date, the 119 notice", len(mine["data"]) >= 5 and "119" in mine["meta"]["notice"]
          and "並非即時監測" in mine["meta"]["notice"] and {x["status"] for x in mine["data"]} >= {"open", "completed", "cancelled"})
    check("patient: no reason, nurse names, notes or results", all(w not in blob for w in ("觀察脫皮範圍", "病人轉院", "測試護理師", "脫皮範圍縮小", "reason", "created_by")))
    check("patient: another patient's list is their own (empty here)", c.get("/api/v1/patients/me/care-follow-ups", headers=H(p2t)).get_json()["data"] == [])
    check("patient cannot use nurse endpoints → 403 (list / detail / create / complete / cancel / reschedule)", all(x == 403 for x in (
        c.get(F, headers=H(pt)).status_code, c.get(f"{F}/{f4.public_id}", headers=H(pt)).status_code, create(pt, rid4)[0].status_code,
        act(pt, f4.public_id, "complete", version=1, result="x", actions="y")[0].status_code,
        act(pt, f4.public_id, "cancel", version=1, reason="x")[0].status_code,
        act(pt, f4.public_id, "reschedule", method="patch", version=1, due_at=tpe(2), reason="x")[0].status_code)))
    check("nurse records list (patient view) has no follow_up key", "follow_up" not in c.get(f"{R}/me", headers=H(pt)).get_json()["data"][0])

    # ================================================================ B. access
    check("unassigned nurse: not in their list; detail / history / write → 404",
          f4.public_id not in [x["id"] for x in c.get(f"{F}?status=all", headers=H(nt2)).get_json()["data"]]
          and all(x == 404 for x in (c.get(f"{F}/{f4.public_id}", headers=H(nt2)).status_code, c.get(f"{F}/{f4.public_id}/events", headers=H(nt2)).status_code,
                                     act(nt2, f4.public_id, "cancel", version=1, reason="x")[0].status_code,
                                     act(nt2, f4.public_id, "complete", version=1, result="x", actions="y")[0].status_code,
                                     act(nt2, f4.public_id, "reschedule", method="patch", version=1, due_at=tpe(2), reason="x")[0].status_code,
                                     create(nt2, rid4)[0].status_code, c.get(f"{F}?patient_id={pid}", headers=H(nt2)).status_code)))
    hidden = c.get(f"{F}/{f4.public_id}", headers=H(nt2)).get_json()["error"]
    missing = c.get(f"{F}/{uuid.uuid4()}", headers=H(nt2)).get_json()["error"]
    hidden_rec = create(nt2, rid4)[1]["error"]
    missing_rec = create(nt2, 999_999)[1]["error"]
    check("…the 404 of a hidden follow-up / record looks exactly like a non-existent one (nothing revealed)",
          (hidden["code"], hidden["message"]) == (missing["code"], missing["message"])
          and (hidden_rec["code"], hidden_rec["message"]) == (missing_rec["code"], missing_rec["message"]))
    check("admin → 403 (no clinical access by role name)", all(x == 403 for x in (c.get(F, headers=H(at)).status_code,
                                                                                c.get(f"{F}/{f4.public_id}", headers=H(at)).status_code,
                                                                                create(at, rid4)[0].status_code,
                                                                                act(at, f4.public_id, "cancel", version=1, reason="x")[0].status_code)))
    assignment = db.session.query(NursePatientAssignment).filter_by(patient_id=p1.id, ended_at=None).first()
    assignment.ended_at = utcnow()
    db.session.commit()
    check("assignment ended → the nurse loses access at once (list empty, detail / write 404)",
          c.get(f"{F}?status=all", headers=H(nt)).get_json()["data"] == [] and c.get(f"{F}/{f4.public_id}", headers=H(nt)).status_code == 404
          and act(nt, f4.public_id, "cancel", version=1, reason="x")[0].status_code == 404)
    assignment.ended_at = None
    db.session.commit()
    check("no Idempotency-Key on a write → 428", c.post(f"{F}/{f4.public_id}/cancel", json={"version": 1, "reason": "x"},
                                                       headers={"Authorization": f"Bearer {nt}"}).status_code == 428)
    all_audit = json.dumps([[a.resource_type, a.changes] for a in db.session.query(AuditLog).filter(AuditLog.resource_type.like("care_follow%"))],
                           ensure_ascii=False, default=str)
    check("audit holds ids, dates, statuses — not the reasons or nursing notes", "病人轉院" not in all_audit and "脫皮範圍縮小" not in all_audit
          and "觀察脫皮範圍" not in all_audit)

    # ================================================================ acceptance 1: Phase 1a next_follow_up_at stays visible
    rid_l = skin_report(pt, skin_itch="yes")
    review(nt, rid_l)
    legacy_a = db.session.get(SymptomRecord, rid_l).nursing_assessment
    legacy_due = (utcnow() + timedelta(days=3)).replace(microsecond=0)
    # a review made by Phase 1a code: the date lives only on the signed review assessment, no follow-up item
    db.session.execute(sql("UPDATE nursing_assessments SET next_follow_up_at = :d WHERE id = :i"), {"d": legacy_due, "i": legacy_a.id})
    db.session.commit()
    before_row = db.session.execute(sql("SELECT next_follow_up_at, updated_at, plan FROM nursing_assessments WHERE id = :i"), {"i": legacy_a.id}).one()
    fl = c.get(f"{R}/{pid}?review_status=follow_up", headers=H(nt)).get_json()
    entry = next((x for x in fl["data"] if x["id"] == rid_l), None)
    check("Phase 1a date without a follow-up item is still listed under 需追蹤 (not lost by the new filter)", entry is not None
          and entry["follow_up"] is None and entry["legacy_follow_up_at"] == legacy_due.strftime("%Y-%m-%dT%H:%M:%S.000Z"), entry)
    check("…reading converts nothing: no follow-up item created, the signed review untouched",
          db.session.query(CareFollowUp).filter_by(symptom_record_id=rid_l).count() == 0
          and db.session.execute(sql("SELECT next_follow_up_at, updated_at, plan FROM nursing_assessments WHERE id = :i"), {"i": legacy_a.id}).one() == before_row)
    check("…counted in meta.follow_up", fl["meta"]["follow_up"] == len(fl["data"]))
    check("records whose follow-up items are all closed are not 需追蹤 (even with a date on the review)",
          rid1 not in [x["id"] for x in fl["data"]] and rid2 not in [x["id"] for x in fl["data"]])
    r, b = create(nt, rid_l, due=tpe(3), reason="依原審閱排定的追蹤")
    fl = c.get(f"{R}/{pid}?review_status=follow_up", headers=H(nt)).get_json()
    entry = next((x for x in fl["data"] if x["id"] == rid_l), None)
    check("a nurse turns it into a follow-up item → listed once, now as the open item (no legacy marker)", r.status_code == 201
          and [x["id"] for x in fl["data"]].count(rid_l) == 1 and entry["follow_up"]["status"] == "open" and entry["legacy_follow_up_at"] is None)
    act(nt, b["data"]["id"], "cancel", version=1, reason="病人已回診")
    fl = c.get(f"{R}/{pid}?review_status=follow_up", headers=H(nt)).get_json()
    check("…cancelled → no longer 需追蹤; the old review date does not bring it back", rid_l not in [x["id"] for x in fl["data"]])
    check("patients never see the legacy marker", "legacy_follow_up_at" not in c.get(f"{R}/me", headers=H(pt)).get_json()["data"][0])

    # ================================================================ acceptance 2: completion = a new, signed, identifiable assessment
    comp_row = db.session.query(CareFollowUp).filter_by(public_id=fu2["id"]).one()
    comp_a = comp_row.completion_assessment
    check("completion assessment: new row, signed, marked 【追蹤結案】 and linked from the follow-up", comp_a.sign_status == "signed"
          and comp_a.subjective.startswith("【追蹤結案】") and comp_a.id != comp_row.review_assessment_id)
    for label, aid in (("completion", comp_a.id), ("review", comp_row.review_assessment_id)):
        r = c.patch(f"/api/v1/nursing-assessments/{aid}", json={"plan": "改寫"}, headers=H(nt))
        check(f"signed {label} assessment cannot be edited → 422 RECORD_LOCKED", r.status_code == 422 and code(r) == "RECORD_LOCKED")
    staff_tl = c.get(f"/api/v1/patients/{pid}/timeline?limit=100", headers=H(nt)).get_json()["data"]
    ev_staff = next(e for e in staff_tl if e["event_id"] == f"NURSING_ASSESSMENT:{comp_a.id}")
    check("staff timeline: titled 追蹤結案 and linked to its follow-up", "追蹤結案" in ev_staff["title"] and ev_staff["detail"]["care_follow_up_id"] == fu2["id"])
    patient_tl = c.get("/api/v1/patients/me/timeline?limit=100", headers=H(pt)).get_json()["data"]
    ev_pt = next(e for e in patient_tl if e["event_id"] == f"NURSING_ASSESSMENT:{comp_a.id}")
    pt_blob = json.dumps(ev_pt, ensure_ascii=False)
    check("patient timeline: a neutral line only — no result, actions, reason, mark or follow-up id", ev_pt["summary"] in ("護理師已追蹤您的狀況。", "護理師已完成評估。")
          and set(ev_pt["detail"]) == {"assessment_type", "assessment_type_text"}
          and not any(w in pt_blob for w in ("脫皮範圍縮小", "持續保濕", "觀察脫皮範圍", "追蹤結案", fu2["id"])), pt_blob)

    # ================================================================ acceptance 4: complete + next follow-up — one transaction
    rid6 = skin_report(pt, skin_itch="yes")
    review(nt, rid6, follow_up={"due_at": tpe(2), "reason": "確認搔癢"})
    f6 = db.session.query(CareFollowUp).filter_by(symptom_record_id=rid6).one()
    counts = lambda: (db.session.query(NursingAssessment).count(), db.session.query(CareFollowUp).count(),
                      db.session.query(CareFollowUpEvent).count(), db.session.query(AuditLog).filter(AuditLog.resource_type == "care_follow_ups").count())
    before = counts()
    real_create = fs.create_follow_up

    def failing_next(record, nurse, due_at, reason, assessment, previous=None):
        if previous is not None:
            raise RuntimeError("simulated failure creating the next follow-up")
        return real_create(record, nurse, due_at, reason, assessment, previous)

    fs.create_follow_up = failing_next
    ckey = str(uuid.uuid4())
    payload = dict(version=1, result="搔癢減輕", actions="持續保濕", next_follow_up={"due_at": tpe(9), "reason": "一週後再確認"})
    try:
        failed = act(nt, f6.public_id, "complete", key=ckey, **payload)[0].status_code == 500
    except RuntimeError:
        failed = True
    fs.create_follow_up = real_create
    db.session.rollback()
    db.session.expire_all()
    f6 = db.session.query(CareFollowUp).filter_by(symptom_record_id=rid6).one()
    check("next follow-up fails → everything rolled back: still open (version 1), no completion assessment, no events, no audit",
          failed and f6.status == "open" and f6.version == 1 and f6.completion_assessment_id is None and counts() == before, (before, counts()))
    r, b = act(nt, f6.public_id, "complete", key=ckey, **payload)
    after = counts()
    check("retry with the same Idempotency-Key (the failed attempt is retryable) → completed once, one next follow-up",
          r.status_code == 200 and b["data"]["status"] == "completed" and b["data"]["next_follow_up"]["status"] == "open"
          and after[0] == before[0] + 1 and after[1] == before[1] + 1 and after[2] == before[2] + 2, (before, after))
    r, b2 = act(nt, f6.public_id, "complete", key=ckey, **payload)
    check("replay again → replayed, nothing new (no second completion, no second next follow-up)", r.status_code == 200
          and r.headers.get("Idempotent-Replayed") == "true" and counts() == after and b2["data"]["next_follow_up_ids"] == b["data"]["next_follow_up_ids"])
    r, _ = act(nt, f6.public_id, "complete", **payload)
    check("a new key on the completed follow-up → 409 FOLLOW_UP_CLOSED", r.status_code == 409 and code(r) == "FOLLOW_UP_CLOSED" and counts() == after)
    next6 = b["data"]["next_follow_up"]["id"]

    # ================================================================ acceptance 3: handover to another nurse
    n1 = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    n2 = db.session.query(User).filter_by(email="nurse02@demo.local").one()
    old = db.session.query(NursePatientAssignment).filter_by(patient_id=p1.id, nurse_id=n1.id, ended_at=None).one()
    old.ended_at = utcnow()
    db.session.add(NursePatientAssignment(nurse_id=n2.id, patient_id=p1.id))
    db.session.commit()
    open_ids = {x["id"] for x in c.get(f"{F}?status=open&per_page=100", headers=H(nt2)).get_json()["data"]}
    check("handover: the new nurse sees the open follow-ups of the patient", {next6, f4.public_id} <= open_ids, open_ids)
    check("handover: the former nurse loses access at once (list empty, detail / write 404)",
          c.get(f"{F}?status=all", headers=H(nt)).get_json()["data"] == [] and c.get(f"{F}/{next6}", headers=H(nt)).status_code == 404
          and act(nt, next6, "cancel", version=1, reason="x")[0].status_code == 404)
    r, b = act(nt2, next6, "reschedule", method="patch", version=1, due_at=tpe(10), reason="交接後重新安排")
    check("handover: the new nurse can act on it (reschedule)", r.status_code == 200 and b["data"]["version"] == 2)
    r, b = act(nt2, next6, "complete", version=2, result="交接後完成追蹤", actions="衛教")
    check("handover: …and complete it; created_by stays the original nurse, completed_by is the new one",
          r.status_code == 200 and b["data"]["created_by"]["display_name"] == "測試護理師 林" and b["data"]["completed_by"]["display_name"] == "測試護理師 陳")
    hist = c.get(f"{F}/{next6}/events", headers=H(nt2)).get_json()["data"]
    check("handover: history keeps every actor and time (林 created, 陳 rescheduled and completed); nothing reset or deleted",
          [(e["event_type"], e["actor"]["display_name"]) for e in hist] == [("created", "測試護理師 林"), ("rescheduled", "測試護理師 陳"), ("completed", "測試護理師 陳")]
          and all(e["occurred_at"] for e in hist) and db.session.query(CareFollowUp).filter_by(public_id=next6).one().previous_follow_up_id == f6.id)
    check("handover: the old open follow-ups were not reset (same version, due date, creator)",
          db.session.query(CareFollowUp).filter_by(public_id=f4.public_id).one().created_by == n1.id
          and db.session.query(CareFollowUp).filter_by(public_id=f4.public_id).one().version == 1)

# ================================================================ migration: empty / existing, protected downgrade
migrations = str(Path(__file__).resolve().parents[1] / "migrations")
for label, existing in (("empty database", False), ("existing database", True)):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        from app.config import DevelopmentConfig
        from flask_migrate import downgrade, upgrade
        from sqlalchemy import inspect

        target = f"sqlite:///{Path(tmp, 'mig.db').as_posix()}"
        saved = (DevelopmentConfig.SQLALCHEMY_DATABASE_URI, DevelopmentConfig.EMAIL_CAPTURE_DIR, DevelopmentConfig.PHOTO_LOCAL_DIR)
        DevelopmentConfig.SQLALCHEMY_DATABASE_URI, DevelopmentConfig.EMAIL_CAPTURE_DIR = target, None
        DevelopmentConfig.PHOTO_LOCAL_DIR = str(Path(tmp, "photos"))
        mig = create_app("development")
        DevelopmentConfig.SQLALCHEMY_DATABASE_URI, DevelopmentConfig.EMAIL_CAPTURE_DIR, DevelopmentConfig.PHOTO_LOCAL_DIR = saved
        with mig.app_context(), warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            if str(db.engine.url) != target:
                raise SystemExit(f"refusing to migrate {db.engine.url}: not the throw-away database")
            tables = ("users", "patient_profiles", "nurse_patient_assignments", "symptom_records", "nursing_assessments", "chat_messages",
                      "symptom_photos", "patient_consents")
            count = lambda: [db.session.execute(sql(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables]
            if existing:
                upgrade(directory=migrations, revision="e7a2c4f9d1b3")  # Phase 1b head
                seed_dev_data(); db.session.commit()
                before = count()
            upgrade(directory=migrations)
            check(f"migration ({label}): creates care_follow_ups + care_follow_up_events",
                  {"care_follow_ups", "care_follow_up_events"} <= set(inspect(db.engine).get_table_names()))
            if existing:
                check(f"migration ({label}): existing data untouched (no next_follow_up_at conversion)", count() == before
                      and db.session.execute(sql("SELECT COUNT(*) FROM care_follow_ups")).scalar() == 0)
                downgrade(directory=migrations, revision="e7a2c4f9d1b3")
                check(f"migration ({label}): downgrade of empty follow-up tables drops only them", count() == before
                      and "care_follow_ups" not in set(inspect(db.engine).get_table_names()))
                upgrade(directory=migrations)
                uid = db.session.execute(sql("SELECT id FROM users WHERE email = 'nurse01@demo.local'")).scalar()
                pid_ = db.session.execute(sql("SELECT id FROM patient_profiles WHERE patient_code = 'P00001'")).scalar()
                now = utcnow()
                db.session.execute(sql("INSERT INTO symptom_records (patient_id, form_id, form_version, recorded_at, reported_by, review_status, source,"
                                       " record_status, created_at, updated_at) VALUES (:p, NULL, NULL, :n, :u, 'reviewed', 'nurse', 'final', :n, :n)"),
                                   {"p": pid_, "n": now, "u": uid})
                srid = db.session.execute(sql("SELECT MAX(id) FROM symptom_records")).scalar()
                db.session.execute(sql("INSERT INTO nursing_assessments (patient_id, assessed_by, assessed_at, assessment_type, sign_status, source,"
                                       " record_status, created_at, updated_at) VALUES (:p, :u, :n, 'follow_up', 'signed', 'nurse', 'final', :n, :n)"),
                                   {"p": pid_, "n": now, "u": uid})
                naid = db.session.execute(sql("SELECT MAX(id) FROM nursing_assessments")).scalar()
                db.session.execute(sql("INSERT INTO care_follow_ups (public_id, patient_id, symptom_record_id, review_assessment_id, due_at, reason, status,"
                                       " version, created_by, created_at, updated_at) VALUES (:g, :p, :s, :a, :n, 'x', 'open', 1, :u, :n, :n)"),
                                   {"g": str(uuid.uuid4()), "p": pid_, "s": srid, "a": naid, "n": now, "u": uid})
                db.session.commit()
                try:
                    downgrade(directory=migrations, revision="e7a2c4f9d1b3")
                    refused = False
                except (RuntimeError, SystemExit):  # Flask-Migrate logs the migration's error and exits
                    refused = True
                db.session.rollback()
                check(f"migration ({label}): downgrade refused while follow-up history exists", refused
                      and db.session.execute(sql("SELECT COUNT(*) FROM care_follow_ups")).scalar() == 1)
                os.environ["ALLOW_DROP_CARE_FOLLOW_UPS"] = "yes"
                try:
                    downgrade(directory=migrations, revision="e7a2c4f9d1b3")
                finally:
                    del os.environ["ALLOW_DROP_CARE_FOLLOW_UPS"]
                check(f"migration ({label}): …only with ALLOW_DROP_CARE_FOLLOW_UPS=yes", "care_follow_ups" not in set(inspect(db.engine).get_table_names()))
                upgrade(directory=migrations)
                check(f"migration ({label}): upgrade again works", "care_follow_ups" in set(inspect(db.engine).get_table_names()))
            db.session.remove()
            db.engine.dispose()

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
