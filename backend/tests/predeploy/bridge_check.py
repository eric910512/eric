"""Pre-deployment check — the BRIDGE release (deployed chat code + only the three Phase 1a～1c migrations) on an
ISOLATED PostgreSQL. Run by run_bridge_checks.sh; never against production (local URLs only).

Modes (MODE env):
  bridge-upgrade  BACKEND = bridge code. DB at b5cd01c11e1d (built by the deployed code): snapshot protected data,
                  run the bridge's migrations, prove the data is unchanged (also: no seed ran — users rows incl.
                  password hashes identical), new tables / constraints exist, old features work.
  full-writes     BACKEND = full release (feature/skin-follow-up). Writes Phase 1a / 1b / 1c data (skin report, consent, review with
                  follow-up, completion + next follow-up) and saves a fingerprint of all new-feature rows to SNAP.
  bridge-serve    BACKEND = bridge code, after the full release ran: old features still served (patient + nurse),
                  and the new-feature rows are byte-for-byte what the full release left (nothing deleted / rewritten).
  full-again      BACKEND = full release: its new features still see the data (follow-ups listed, chain intact).
"""
import hashlib
import json
import os
import secrets
import sys
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

url = os.environ.get("DATABASE_URL", "")
if urlparse(url).hostname not in ("127.0.0.1", "localhost") or not url.startswith("postgresql"):
    raise SystemExit("refusing: DATABASE_URL must be a local PostgreSQL")
MODE = os.environ["MODE"]
BACKEND = os.environ["BACKEND"]
sys.path.insert(0, BACKEND)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
PASSWORD = "Predeploy123"
os.environ.update(FLASK_ENV="staging", SEED_DEMO_PASSWORD=PASSWORD, CORS_ORIGINS="https://predeploy.invalid")
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_hex(32))
for k in ("PHOTO_UPLOADS_ENABLED", "PHOTO_STORAGE_BACKEND", "SEED_DEMO_DATA"):
    os.environ.pop(k, None)

from sqlalchemy import inspect, text  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] [{MODE}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("staging")
c = app.test_client()
PROTECTED = ("users", "patient_profiles", "nurse_patient_assignments", "chat_messages", "chat_read_states",
             "symptom_records", "symptom_record_values", "nursing_assessments", "notifications")
NEW_FEATURE = ("care_follow_ups", "care_follow_up_events", "patient_consents", "symptom_photos")


def token(email):
    return c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).get_json()["data"]["access_token"]


def H(t):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": str(uuid.uuid4())}


def fingerprint(tables, where=None):
    out = {}
    for t in tables:
        rows = db.session.execute(text(f"SELECT * FROM {t} {where.get(t, '') if where else ''} ORDER BY id")).all()
        out[t] = [len(rows), hashlib.sha256(repr(rows).encode()).hexdigest()[:16]]
    return out


def q(sql):
    return db.session.execute(text(sql)).scalar()


def release():
    db.session.remove()
    db.engine.dispose()


SKIN_ROWS = {"symptom_records": "WHERE form_id = (SELECT id FROM symptom_forms WHERE code = 'rt_skin_self_assessment')",
             "symptom_record_values": "WHERE symptom_record_id IN (SELECT r.id FROM symptom_records r JOIN symptom_forms f ON f.id = r.form_id"
                                      " WHERE f.code = 'rt_skin_self_assessment')"}

with app.app_context(), warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    if MODE == "bridge-upgrade":
        from flask_migrate import upgrade

        check("start: database at the chat revision b5cd01c11e1d (SIMULATED baseline built by 71e9d8e)", q("SELECT version_num FROM alembic_version") == "b5cd01c11e1d")
        before = fingerprint(PROTECTED)
        release()
        upgrade(directory=os.path.join(BACKEND, "migrations"))
        release()
        check("bridge `flask db upgrade` → a9d4e2b7c5f1", q("SELECT version_num FROM alembic_version") == "a9d4e2b7c5f1")
        check("protected data byte-for-byte unchanged — incl. users (password hashes, updated_at): no seed ran",
              fingerprint(PROTECTED) == before, {k: v for k, v in fingerprint(PROTECTED).items() if before[k] != v})
        names = set(inspect(db.session.connection()).get_table_names())
        check("new tables exist", set(NEW_FEATURE) <= names)
        idx = {r[0] for r in db.session.execute(text("SELECT indexname FROM pg_indexes WHERE tablename IN ('care_follow_ups','patient_consents','symptom_photos')"))}
        check("partial unique indexes exist (open follow-up, active consent)", {"uq_care_follow_ups_open_record", "uq_patient_consents_active"} <= idx)
        cons = {r[0] for r in db.session.execute(text("SELECT conname FROM pg_constraint WHERE conrelid IN ('care_follow_ups'::regclass, 'symptom_photos'::regclass, 'patient_consents'::regclass)"))}
        check("check / foreign-key constraints exist", {"ck_care_follow_ups_status_fields", "fk_care_follow_ups_review_assessment_id", "ck_symptom_photos_status_fields",
                                                        "ck_patient_consents_revoked_fields"} <= cons)
        check("skin form rows created by the data migration (6 items)", q("SELECT COUNT(*) FROM symptom_form_items i JOIN symptom_forms f ON f.id = i.form_id WHERE f.code = 'rt_skin_self_assessment'") == 6)
        check("50 training pairs still there (100 accounts)", q("SELECT COUNT(*) FROM users WHERE email LIKE 'trpt%' OR email LIKE 'trnu%'") == 100)
        logins = [c.post("/api/v1/auth/login", json={"email": f"{p}{n:03d}@demo.local", "password": PASSWORD}).status_code
                  for p in ("trpt", "trnu") for n in (1, 25, 50)]
        check("training accounts log in (sample of 6)", logins == [200] * 6, logins)
        t2, n2 = token("trpt002@demo.local"), token("trnu002@demo.local")
        r = c.post("/api/v1/patients/me/messages", json={"body": "橋接版本上線後"}, headers=H(t2))
        check("bridge: chat send → 201", r.status_code == 201)
        inbox = c.get("/api/v1/chat/inbox", headers=H(n2)).get_json()["data"]
        check("bridge: the pair's nurse sees it unread", any(x["unread_count"] >= 1 for x in inbox))
        tp2 = q("SELECT public_id FROM patient_profiles WHERE display_name = '教學病人 002'")
        check("bridge: another patient → 404 on that conversation", c.get(f"/api/v1/patients/{tp2}/messages", headers=H(token("trpt003@demo.local"))).status_code == 404)
        daily = [{"definition_code": k, ("value_numeric" if isinstance(v, int) else "option_code"): v} for k, v in dict(
            rt_pain_skin=1, rt_pain_oral=1, rt_dermatitis_redness="none", rt_dermatitis_desquamation="none", rt_appetite_poor=1, rt_fatigue=1,
            self_care_moisturizer="yes", self_care_towel_pat="yes", self_care_mouth_rinse="yes").items()]
        r1 = c.post("/api/v1/symptoms/records", json={"patient_id": "me", "form_code": "rt_daily_report", "values": daily}, headers=H(t2))
        r2 = c.post("/api/v1/symptoms/records", json={"patient_id": "me", "form_code": "rt_daily_report", "values": daily}, headers=H(t2))
        check("bridge: daily report 201, second the same day 409", (r1.status_code, r2.status_code) == (201, 409))
        check("bridge: the new tables are untouched by the old code (all empty)", all(q(f"SELECT COUNT(*) FROM {t}") == 0 for t in NEW_FEATURE))

    elif MODE == "full-writes":
        t1, n1 = token("trpt001@demo.local"), token("trnu001@demo.local")
        r = c.post("/api/v1/patients/me/photo-consent", json={"version": c.get("/api/v1/symptom-photos/settings", headers=H(t1)).get_json()["data"]["consent"]["version"]}, headers=H(t1))
        check("full: patient accepts the photo consent (1b row) → 201", r.status_code == 201)
        rid = c.post("/api/v1/symptoms/records", json={"patient_id": "me", "form_code": "rt_skin_self_assessment",
                                                      "values": [{"definition_code": "skin_itch", "option_code": "yes"}], "notes": "橋接測試"}, headers=H(t1)).get_json()["data"]["id"]
        due = (datetime.now(timezone(timedelta(hours=8))) + timedelta(days=2)).replace(microsecond=0).isoformat()
        r = c.post(f"/api/v1/symptoms/records/{rid}/review", json={"action_note": "已衛教", "follow_up": {"due_at": due, "reason": "兩天後確認"}}, headers=H(n1))
        fid = r.get_json()["data"]["follow_up"]["id"]
        nxt = (datetime.now(timezone(timedelta(hours=8))) + timedelta(days=9)).replace(microsecond=0).isoformat()
        r = c.post(f"/api/v1/care-follow-ups/{fid}/complete", json={"result": "改善", "actions": "保濕", "version": 1,
                                                                   "next_follow_up": {"due_at": nxt, "reason": "一週後再確認"}}, headers=H(n1))
        check("full: skin report, review + follow-up, completion + next follow-up", r.status_code == 200 and r.get_json()["data"]["next_follow_up"]["status"] == "open")
        snap = {**fingerprint(NEW_FEATURE), **fingerprint(("symptom_records", "symptom_record_values"), SKIN_ROWS)}
        check("full: new-feature rows written", snap["care_follow_ups"][0] == 2 and snap["care_follow_up_events"][0] == 3 and snap["patient_consents"][0] == 1, snap)
        json.dump(snap, open(os.environ["SNAP"], "w"))

    elif MODE == "bridge-serve":
        t1, n1 = token("trpt001@demo.local"), token("trnu001@demo.local")
        tp1 = q("SELECT public_id FROM patient_profiles WHERE display_name = '教學病人 001'")
        patient = {p: c.get(p, headers=H(t1)).status_code for p in ("/api/v1/patients/me/messages", "/api/v1/symptoms/records/me", "/api/v1/dashboard/patient/me",
                                                                    "/api/v1/patients/me/timeline", "/api/v1/chat/unread-count", "/api/v1/notifications")}
        check("bridge after the full release: patient endpoints of the old features → 200", set(patient.values()) == {200}, patient)
        nurse = {p: c.get(p, headers=H(n1)).status_code for p in (f"/api/v1/symptoms/records/{tp1}", f"/api/v1/dashboard/patient/{tp1}",
                                                                  "/api/v1/dashboard/widgets/pending-symptom-reviews/data", f"/api/v1/patients/{tp1}/timeline",
                                                                  f"/api/v1/nursing-assessments?patient_id={tp1}", "/api/v1/chat/inbox", f"/api/v1/patients/{tp1}/messages")}
        check("bridge after the full release: nurse endpoints of the old features → 200", set(nurse.values()) == {200}, nurse)
        recs = c.get("/api/v1/symptoms/records/me", headers=H(t1)).get_json()["data"]
        check("bridge lists the skin report the full release created (read only through the old screens)", any(r["form"] and r["form"]["code"] == "rt_skin_self_assessment" for r in recs))
        r = c.post("/api/v1/patients/me/messages", json={"body": "回到橋接版本後"}, headers=H(t1))
        check("bridge after the full release: chat send → 201", r.status_code == 201)
        snap = json.load(open(os.environ["SNAP"]))
        now = {**fingerprint(NEW_FEATURE), **fingerprint(("symptom_records", "symptom_record_values"), SKIN_ROWS)}
        check("new-feature rows byte-for-byte as the full release left them (not deleted, not rewritten)", now == snap, {k: (snap[k], now[k]) for k in now if snap[k] != now[k]})

    elif MODE == "full-again":
        n1 = token("trnu001@demo.local")
        items = c.get("/api/v1/care-follow-ups?status=all", headers=H(n1)).get_json()["data"]
        check("full release again: its follow-ups are all there (completed + open next, chained)", len(items) == 2
              and {i["status"] for i in items} == {"completed", "open"} and any(i["previous_follow_up_id"] for i in items))
        t1 = token("trpt001@demo.local")
        check("full release again: the patient's consent is still accepted", c.get("/api/v1/symptom-photos/settings", headers=H(t1)).get_json()["data"]["consent"]["accepted"] is True)
    release()

print(f"{sum(ok)}/{len(ok)} [{MODE}] checks passed")
sys.exit(0 if all(ok) else 1)
