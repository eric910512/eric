"""Pre-deployment check — Phase 1a～1c upgrade path on an ISOLATED PostgreSQL (never production).

SIMULATED BASELINE: a database at the chat revision ``b5cd01c11e1d`` built by ``baseline_build.py`` with the
deployed chat code (71e9d8e): demo seed, 50 training pairs, chat messages / read states, a daily report and its
review. It is NOT the production database; the production revision is verified separately.

Steps: upgrade b5cd → c3e9 (1a) → e7a2 (1b) → a9d4 (1c), each time checking that the protected data
(users, patients, assignments, chat, symptom records / values, nursing assessments, rt_daily_report form)
is byte-for-byte unchanged and the new objects exist; then no schema drift on PostgreSQL; then a
production-like smoke (FLASK_ENV=staging, photos off by default) of the new features and of chat; then the
downgrade guard. The script leaves the database at the head for the old-code checks run afterwards.

Usage (from backend/): DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:<port>/postgres python tests/predeploy/upgrade_path_check.py
Refuses any DATABASE_URL that is not on 127.0.0.1 / localhost. Not part of tests/run_all.py.
"""
import hashlib
import io
import os
import secrets
import sys
import uuid
import warnings
from pathlib import Path
from urllib.parse import urlparse

url = os.environ.get("DATABASE_URL", "")
if urlparse(url).hostname not in ("127.0.0.1", "localhost") or not url.startswith("postgresql"):
    raise SystemExit(f"refusing: DATABASE_URL must be a local PostgreSQL (got host {urlparse(url).hostname!r})")
BACKEND = os.environ.get("BACKEND") or str(Path(__file__).resolve().parents[2])  # the code under test (default: this checkout)
sys.path.insert(0, BACKEND)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
PASSWORD = "Predeploy123"
os.environ.update(FLASK_ENV="staging", SECRET_KEY=secrets.token_hex(32), JWT_SECRET_KEY=secrets.token_hex(32),
                  CORS_ORIGINS="https://predeploy.invalid", SEED_DEMO_PASSWORD=PASSWORD, EMAIL_PROVIDER="disabled")
os.environ.pop("PHOTO_UPLOADS_ENABLED", None)
os.environ.pop("PHOTO_STORAGE_BACKEND", None)

from flask_migrate import downgrade, upgrade  # noqa: E402
from sqlalchemy import inspect, text  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("staging")
c = app.test_client()
MIG = str(Path(BACKEND) / "migrations")
PROTECTED = ("users", "patient_profiles", "nurse_patient_assignments", "chat_messages", "chat_read_states",
             "symptom_records", "symptom_record_values", "nursing_assessments", "notifications")


def token(email):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return r.get_json()["data"]["access_token"]


def H(t):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": str(uuid.uuid4())}


def snapshot():
    out = {}
    for t in PROTECTED:
        rows = db.session.execute(text(f"SELECT * FROM {t} ORDER BY id")).all()
        out[t] = (len(rows), hashlib.sha256(repr(rows).encode()).hexdigest()[:16])
    daily = db.session.execute(text(
        "SELECT d.code, d.name_zh, i.display_order, i.is_required FROM symptom_form_items i JOIN symptom_forms f ON f.id = i.form_id"
        " JOIN symptom_definitions d ON d.id = i.definition_id WHERE f.code = 'rt_daily_report' ORDER BY i.display_order")).all()
    out["rt_daily_report"] = (len(daily), hashlib.sha256(repr(daily).encode()).hexdigest()[:16])
    return out


def release():
    db.session.remove()
    db.engine.dispose()


def revision():
    return db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()


with app.app_context(), warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    if str(db.engine.url.host) not in ("127.0.0.1", "localhost"):
        raise SystemExit("refusing: engine is not local")
    print(f"database: {db.engine.dialect.name} {db.engine.url.host}:{db.engine.url.port} (isolated)")

    # ---------------------------------------------------------------- simulated production baseline (built by the deployed code)
    check("baseline: database at the chat revision b5cd01c11e1d (SIMULATED, built by 71e9d8e)", revision() == "b5cd01c11e1d")
    if revision() != "b5cd01c11e1d":
        raise SystemExit("run baseline_build.py with the deployed code first")
    pairs = db.session.execute(text("SELECT COUNT(*) FROM users WHERE email LIKE 'trpt%' OR email LIKE 'trnu%'")).scalar()
    check("baseline: 50 training pairs (100 accounts)", pairs == 100, pairs)
    pt, nt = token("patient01@demo.local"), token("nurse01@demo.local")
    t1, tn1 = token("trpt001@demo.local"), token("trnu001@demo.local")
    daily = [{"definition_code": k, ("value_numeric" if isinstance(v, int) else "option_code"): v} for k, v in dict(
        rt_pain_skin=2, rt_pain_oral=1, rt_dermatitis_redness="light", rt_dermatitis_desquamation="dry", rt_appetite_poor=1, rt_fatigue=2,
        self_care_moisturizer="yes", self_care_towel_pat="yes", self_care_mouth_rinse="yes").items()]
    # the new code must never serve before its migrations: on the old schema its symptom endpoints fail (500)
    early = c.get("/api/v1/symptoms/records/me", headers=H(pt)).status_code
    db.session.rollback()
    check("FINDING: new code on the old schema (migrations not run) → symptom endpoints 500 — the upgrade must run first",
          early == 500, early)
    base = snapshot()
    check("baseline: chat messages, read states, a daily report and its review exist", base["chat_messages"][0] >= 4
          and base["chat_read_states"][0] >= 1 and base["symptom_records"][0] >= 1 and base["nursing_assessments"][0] >= 1, base)

    # ---------------------------------------------------------------- stepwise upgrade
    for rev, label, expect_tables in (("c3e9a1f4b7d2", "Phase 1a", set()), ("e7a2c4f9d1b3", "Phase 1b", {"symptom_photos", "patient_consents"}),
                                      ("a9d4e2b7c5f1", "Phase 1c", {"care_follow_ups", "care_follow_up_events"})):
        release()  # PGlite takes one connection at a time: Alembic opens its own
        upgrade(directory=MIG, revision=rev)
        db.session.remove()
        check(f"{label}: upgrade to {rev} on PostgreSQL", revision() == rev)
        check(f"{label}: protected data byte-for-byte unchanged (users, patients, assignments, chat, records, assessments, rt_daily_report)",
              snapshot() == base, {k: v for k, v in snapshot().items() if base.get(k) != v})
        if expect_tables:
            check(f"{label}: new tables exist {sorted(expect_tables)}", expect_tables <= set(inspect(db.session.connection()).get_table_names()))
    skin = db.session.execute(text("SELECT COUNT(*) FROM symptom_form_items i JOIN symptom_forms f ON f.id = i.form_id WHERE f.code = 'rt_skin_self_assessment'")).scalar()
    check("Phase 1a: skin form created (6 items)", skin == 6)
    idx = {r[0] for r in db.session.execute(text("SELECT indexname FROM pg_indexes WHERE tablename IN ('care_follow_ups', 'care_follow_up_events', 'symptom_photos', 'patient_consents')"))}
    want_idx = {"uq_care_follow_ups_open_record", "ix_care_follow_ups_patient_id_status_due_at", "ix_care_follow_ups_status_due_at",
                "ix_care_follow_up_events_follow_up_id_id", "uq_patient_consents_active", "ix_symptom_photos_status_created_at"}
    check("indexes present (incl. partial unique open-follow-up and active-consent indexes)", want_idx <= idx, sorted(want_idx - idx))
    partial = db.session.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_care_follow_ups_open_record'")).scalar()
    check("…the open-follow-up index is partial on PostgreSQL (WHERE status = 'open')", "WHERE" in partial and "open" in partial, partial)
    cons = {r[0] for r in db.session.execute(text("SELECT conname FROM pg_constraint WHERE conrelid IN ('care_follow_ups'::regclass, 'symptom_photos'::regclass)"))}
    check("check / foreign-key constraints present", {"ck_care_follow_ups_status_fields", "fk_care_follow_ups_review_assessment_id",
                                                      "ck_symptom_photos_status_fields"} <= cons)

    release()
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    with db.engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), db.metadata)
    check("no schema drift on PostgreSQL (models == migrated schema)", diffs == [], diffs[:3])

    # ---------------------------------------------------------------- production-like smoke (staging, photos off)
    s = c.get("/api/v1/symptom-photos/settings", headers=H(t1)).get_json()["data"]
    check("photos OFF by default in staging (disabled_by_config, no storage)", s["upload_enabled"] is False and s["view_enabled"] is False
          and s["reason"] == "disabled_by_config", s["reason"])
    r = c.post("/api/v1/patients/me/symptom-photos", data={"file": (io.BytesIO(b"\xff\xd8\xff" + b"0" * 100), "a.jpg", "image/jpeg")},
               headers=H(t1), content_type="multipart/form-data")
    check("photo upload refused → 503 PHOTO_UPLOADS_DISABLED", r.status_code == 503 and r.get_json()["error"]["code"] == "PHOTO_UPLOADS_DISABLED")
    r = c.post("/api/v1/symptoms/records", json={"patient_id": "me", "form_code": "rt_skin_self_assessment",
                                                 "values": [{"definition_code": "skin_itch", "option_code": "yes"}]}, headers=H(t1))
    skin_id = r.get_json()["data"]["id"] if r.status_code == 201 else None
    check("training patient: text skin self-assessment → 201", r.status_code == 201)
    tp1 = db.session.execute(text("SELECT public_id FROM patient_profiles WHERE display_name = '教學病人 001'")).scalar()
    from datetime import datetime, timedelta, timezone
    due = (datetime.now(timezone(timedelta(hours=8))) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0).isoformat()
    r = c.post(f"/api/v1/symptoms/records/{skin_id}/review", json={"action_note": "已衛教", "follow_up": {"due_at": due, "reason": "兩天後確認"}}, headers=H(tn1))
    check("training nurse: review + follow-up → 200", r.status_code == 200 and r.get_json()["data"]["follow_up"]["status"] == "open")
    fid = r.get_json()["data"]["follow_up"]["id"]
    r = c.post(f"/api/v1/care-follow-ups/{fid}/complete", json={"result": "改善", "actions": "持續保濕", "version": 1}, headers=H(tn1))
    check("training nurse: complete the follow-up (new signed assessment) → 200", r.status_code == 200 and r.get_json()["data"]["status"] == "completed")
    tn2 = token("trnu002@demo.local")
    check("another training nurse (other pair) → 404 on that follow-up", c.get(f"/api/v1/care-follow-ups/{fid}", headers=H(tn2)).status_code == 404)
    check("patient sees own follow-ups (read only)", c.get("/api/v1/patients/me/care-follow-ups", headers=H(t1)).get_json()["data"][0]["status"] == "completed")
    r = c.post("/api/v1/patients/me/messages", json={"body": "升級後訊息"}, headers=H(t1))
    check("chat after the upgrade: patient sends → 201", r.status_code == 201)
    inbox = c.get("/api/v1/chat/inbox", headers=H(tn1)).get_json()["data"]
    check("chat: the pair's nurse sees it in the inbox with unread", any(x["patient"]["id"] == tp1 and x["unread_count"] >= 1 for x in inbox))
    check("chat: history from before the upgrade still there", any("baseline：教學病人訊息" == m["body"] for m in
                                                            c.get("/api/v1/patients/me/messages", headers=H(t1)).get_json()["data"]))
    t2 = token("trpt002@demo.local")
    check("chat isolation: another patient → 404 on this patient's conversation", c.get(f"/api/v1/patients/{tp1}/messages", headers=H(t2)).status_code == 404)
    r = c.post("/api/v1/symptoms/records", json={"patient_id": "me", "form_code": "rt_daily_report", "values": daily}, headers=H(pt))
    check("daily report: still once per day (409 ALREADY_REPORTED_TODAY)", r.status_code == 409)
    check("seed is not run by any of this (SEED_DEMO_DATA unset in this process)", os.environ.get("SEED_DEMO_DATA") is None)

    # ---------------------------------------------------------------- downgrade guard (history exists)
    release()
    try:
        downgrade(directory=MIG, revision="e7a2c4f9d1b3")
        refused = False
    except (RuntimeError, SystemExit):
        refused = True
    db.session.rollback()
    check("downgrade 1c → 1b refused while follow-up history exists", refused and revision() == "a9d4e2b7c5f1")
    db.session.remove()
    db.engine.dispose()

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
