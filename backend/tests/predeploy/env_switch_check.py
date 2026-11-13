"""FLASK_ENV switch rehearsal: staging → production on the SAME database — a throw-away PostgreSQL with synthetic
data only, never a deployed one. Every step runs in its own process with the same secrets (like a Render restart
after changing one environment variable).

    PG_BIN=<PostgreSQL bin directory> python tests/predeploy/env_switch_check.py     # from backend/
    (default PostgreSQL: the local `pgserver` package)

  1. STAGING (today's Render): `flask db upgrade` + `flask seed dev` (synthetic accounts, as the demo / training
     data was loaded), then a patient signs in and keeps the session (access + refresh token).
  2. PRODUCTION on that database:
     - the start script: `flask db upgrade` is a no-op; `flask seed dev` is REFUSED (so SEED_DEMO_DATA=true would
       stop the deploy instead of seeding);
     - the staging session still works (same secrets) and can be refreshed; every account signs in again;
     - chat (patient → nurse → reply, unread), daily report (201, then 409 the same day), nurse review queue and
       review, care follow-ups, timeline;
     - photos off by default (disabled_by_config); consent refused (F5);
     - training-account batches refused (403 TRAINING_ACCOUNTS_DISABLED).
  3. Photo switch matrix (configuration only, no upload): uploads ON + database storage + the DRAFT consent version
     → staging turns photos ON, production keeps them OFF (consent_not_approved).
  4. ROLLBACK: STAGING again on the same database — everything written in production is still served.
"""
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
PASSWORD = "Predeploy123"  # synthetic accounts only
PHASE = os.environ.get("ENV_CHECK_PHASE")


# =============================================================================== phases (child processes)
def phase_main():
    import warnings

    sys.path.insert(0, str(BACKEND))
    from sqlalchemy import text

    from app import create_app
    from app.extensions import db

    ok = []
    env_name = os.environ["FLASK_ENV"]

    def check(label, cond, detail=""):
        ok.append(bool(cond))
        print(f"[{'PASS' if cond else 'FAIL'}] [{PHASE}] {label}" + (f"  → {detail}" if detail else ""))

    app = create_app(env_name)
    c = app.test_client()
    state_file = Path(os.environ["ENV_CHECK_STATE"])
    state = json.loads(state_file.read_text()) if state_file.exists() else {}

    def login(email):
        r = c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        return r.status_code, (r.get_json() or {}).get("data", {}).get("access_token"), r.headers.getlist("Set-Cookie")

    def H(t, key=True):
        h = {"Authorization": f"Bearer {t}"}
        if key:
            h["Idempotency-Key"] = str(uuid.uuid4())
        return h

    with app.app_context(), warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        q = lambda sql, **kw: db.session.execute(text(sql), kw).scalar()  # noqa: E731
        check(f"app config is {env_name} (CONFIG_NAME), demo seed allowed = {env_name == 'staging'}",
              app.config["CONFIG_NAME"] == env_name and app.config["ALLOW_DEMO_SEED"] is (env_name == "staging"))

        if PHASE == "staging-session":
            st, tok, cookies = login("patient01@demo.local")
            refresh = next((ck.split(";")[0].split("=", 1)[1] for ck in cookies if ck.startswith("refresh_token=")), None)
            check("staging: patient signs in (session kept for the switch)", st == 200 and tok and refresh)
            state.update(staging_token=tok, staging_refresh=refresh, users=q("SELECT COUNT(*) FROM users"),
                         consents=q("SELECT COUNT(*) FROM patient_consents"))
            state_file.write_text(json.dumps(state))

        elif PHASE == "production":
            check("production: no seed ran, accounts unchanged", q("SELECT COUNT(*) FROM users") == state["users"])
            r = c.get("/api/v1/auth/me", headers=H(state["staging_token"], key=False))
            check("production: the access token issued under staging still works (same secrets)", r.status_code == 200, r.status_code)
            c.set_cookie("refresh_token", state["staging_refresh"], path="/api/v1/auth")
            r = c.post("/api/v1/auth/refresh", headers={"X-Requested-With": "XMLHttpRequest"})
            check("production: the staging session can be refreshed", r.status_code == 200, r.status_code)
            logins = {e: login(e)[0] for e in ("patient01@demo.local", "nurse01@demo.local", "admin01@demo.local")}
            check("production: existing patient / nurse / admin accounts sign in", set(logins.values()) == {200}, logins)
            pt, nt, at = (login(e)[1] for e in ("patient01@demo.local", "nurse01@demo.local", "admin01@demo.local"))
            pid = q("SELECT p.public_id FROM patient_profiles p JOIN users u ON u.id = p.user_id WHERE u.email = 'patient01@demo.local'")

            r = c.post("/api/v1/patients/me/messages", json={"body": "切換 production 後的訊息"}, headers=H(pt))
            check("production chat: patient sends → 201", r.status_code == 201, r.status_code)
            inbox = c.get("/api/v1/chat/inbox", headers=H(nt, key=False))
            check("production chat: nurse inbox → 200 with an unread conversation", inbox.status_code == 200
                  and any(x["unread_count"] >= 1 for x in inbox.get_json()["data"]))
            r = c.post(f"/api/v1/patients/{pid}/messages", json={"body": "護理師回覆"}, headers=H(nt))
            u = c.get("/api/v1/chat/unread-count", headers=H(pt, key=False))
            check("production chat: nurse replies → 201, patient unread count → 200", r.status_code == 201 and u.status_code == 200,
                  (r.status_code, u.status_code))

            had_today = q("SELECT COUNT(*) FROM symptom_records r JOIN symptom_forms f ON f.id = r.form_id JOIN patient_profiles p ON p.id = r.patient_id "
                          "WHERE f.code = 'rt_daily_report' AND p.public_id = :pid AND r.created_at > now() - interval '20 hours'", pid=pid)
            daily = [{"definition_code": k, ("value_numeric" if isinstance(v, int) else "option_code"): v} for k, v in dict(
                rt_pain_skin=1, rt_pain_oral=1, rt_dermatitis_redness="none", rt_dermatitis_desquamation="none", rt_appetite_poor=1,
                rt_fatigue=1, self_care_moisturizer="yes", self_care_towel_pat="yes", self_care_mouth_rinse="yes").items()]
            body = {"patient_id": "me", "form_code": "rt_daily_report", "values": daily}
            r1 = c.post("/api/v1/symptoms/records", json=body, headers=H(pt))
            r2 = c.post("/api/v1/symptoms/records", json=body, headers=H(pt))
            check("production daily report: first → 201 (or 409 if the seed already filed today's), second the same day → 409",
                  r2.status_code == 409 and r1.status_code in ((409,) if had_today else (201,)), (r1.status_code, r2.status_code, had_today))
            skin = c.post("/api/v1/symptoms/records", json={"patient_id": "me", "form_code": "rt_skin_self_assessment",
                                                             "values": [{"definition_code": "skin_itch", "option_code": "yes"}]}, headers=H(pt))
            check("production skin self-assessment (text only) → 201", skin.status_code == 201, skin.status_code)
            queue = c.get("/api/v1/dashboard/widgets/pending-symptom-reviews/data", headers=H(nt, key=False))
            check("production nurse review queue → 200", queue.status_code == 200, queue.status_code)
            rid = skin.get_json()["data"]["id"]
            r = c.post(f"/api/v1/symptoms/records/{rid}/review", json={"action_note": "已衛教"}, headers=H(nt))
            check("production nurse review → 200", r.status_code == 200, r.status_code)
            fu = c.get("/api/v1/care-follow-ups?status=all", headers=H(nt, key=False))
            tl = c.get(f"/api/v1/patients/{pid}/timeline", headers=H(nt, key=False))
            check("production care follow-ups and timeline → 200", fu.status_code == 200 and tl.status_code == 200, (fu.status_code, tl.status_code))

            s = c.get("/api/v1/symptom-photos/settings", headers=H(pt, key=False)).get_json()["data"]
            check("production photos: off by default (disabled_by_config)", s["upload_enabled"] is False and s["reason"] == "disabled_by_config")
            r = c.post("/api/v1/patients/me/photo-consent", json={"version": s["consent"]["version"]}, headers=H(pt))
            check("production photos: consent refused (503 PHOTO_UPLOADS_DISABLED), no row written",
                  r.status_code == 503 and q("SELECT COUNT(*) FROM patient_consents") == state["consents"], r.status_code)
            r = c.post("/api/v1/admin/training-accounts/preview", json={"start": 1, "count": 1}, headers=H(at))
            check("production: training-account batches refused (403 TRAINING_ACCOUNTS_DISABLED)",
                  r.status_code == 403 and (r.get_json() or {}).get("error", {}).get("code") == "TRAINING_ACCOUNTS_DISABLED", r.status_code)
            state.update(prod_message="切換 production 後的訊息", prod_record=rid)
            state_file.write_text(json.dumps(state))

        elif PHASE == "staging-again":
            pt = login("patient01@demo.local")[1]
            msgs = c.get("/api/v1/patients/me/messages?limit=30", headers=H(pt, key=False)).get_json()["data"]
            recs = c.get("/api/v1/symptoms/records/me", headers=H(pt, key=False)).get_json()["data"]
            check("rollback to staging: messages and the reviewed report written under production are served",
                  any(m["body"] == state["prod_message"] for m in msgs) and any(str(r["id"]) == str(state["prod_record"]) for r in recs))

        elif PHASE == "photo-gate":
            f = app.extensions["photo_feature"]
            print(f"[INFO] [{PHASE}] {env_name}: upload_enabled={f.upload_enabled} reason={f.reason}")
            expect_on = env_name == "staging"
            check(f"{env_name} + PHOTO_UPLOADS_ENABLED=true + database + the DRAFT consent version → uploads "
                  f"{'ON (staging does not refuse a draft)' if expect_on else 'OFF (consent_not_approved)'}",
                  f.upload_enabled is expect_on and (expect_on or f.reason == "consent_not_approved"))
    print(f"{sum(ok)}/{len(ok)} [{PHASE}] checks passed")
    sys.exit(0 if all(ok) else 1)


# =============================================================================== orchestrator
def free_port():
    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        return s_.getsockname()[1]


def main():
    if os.environ.get("PG_BIN"):
        pgbin = Path(os.environ["PG_BIN"])
    else:
        from pgserver import _commands  # local test tool
        pgbin = Path(_commands.POSTGRES_BIN_PATH)
    exe = lambda name: str(pgbin / (name + (".exe" if os.name == "nt" else "")))  # noqa: E731
    tmp = Path(tempfile.mkdtemp(prefix="env-switch-check-"))
    port = free_port()
    version = subprocess.run([exe("postgres"), "--version"], capture_output=True, text=True).stdout.strip()
    print(f"===== FLASK_ENV switch staging → production on a throw-away PostgreSQL ({version}), synthetic data")
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    subprocess.run([exe("initdb"), "-D", str(tmp / "data"), "-U", "postgres", "-A", "trust", "-E", "UTF8"], check=True, **quiet)
    subprocess.run([exe("pg_ctl"), "-D", str(tmp / "data"), "-o", f"-p {port} -h 127.0.0.1", "-l", str(tmp / "pg.log"), "-w", "start"],
                   check=True, **quiet)
    subprocess.run([exe("createdb"), "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "ccp"], check=True, **quiet)
    consent = re.search(r'^CONSENT_VERSION = "([^"]+)"', (BACKEND / "app/modules/photo/consent.py").read_text(encoding="utf-8"), re.M).group(1)
    base = {k: v for k, v in os.environ.items() if not k.startswith(("PHOTO_", "AWS_", "SEED_", "DATABASE_", "FLASK_"))}
    base.update(CORS_ORIGINS="https://env-switch.invalid", PYTHONIOENCODING="utf-8", SEED_DEMO_PASSWORD=PASSWORD,
                SECRET_KEY=secrets.token_hex(32), JWT_SECRET_KEY=secrets.token_hex(32),
                DATABASE_URL=f"postgresql://postgres@127.0.0.1:{port}/ccp", ENV_CHECK_STATE=str(tmp / "state.json"))
    results = []

    def run(args, env_name, **extra):
        return subprocess.run(args, cwd=BACKEND, env={**base, "FLASK_ENV": env_name, **extra}, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", timeout=900)

    def expect(label, cond, detail=""):
        results.append(bool(cond))
        print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))

    def phase(name, env_name, **extra):
        p = run([sys.executable, __file__], env_name, ENV_CHECK_PHASE=name, **extra)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith(("[PASS]", "[FAIL]", "[INFO]"))]
        print("\n".join(lines))
        results.extend(ln.startswith("[PASS]") for ln in lines if not ln.startswith("[INFO]"))
        if p.returncode != 0 and not any(ln.startswith("[FAIL]") for ln in lines):
            print(f"[FAIL] phase {name} crashed (exit {p.returncode})\n{p.stderr[-2000:]}")
            results.append(False)

    try:
        flask = [sys.executable, "-m", "flask", "--app", "run"]
        print("----- 1. STAGING (today's Render)")
        p = run(flask + ["db", "upgrade"], "staging")
        expect("staging: flask db upgrade → head", p.returncode == 0, p.stderr[-300:] if p.returncode else "")
        p = run(flask + ["seed", "dev"], "staging")
        expect("staging: flask seed dev (synthetic accounts) → allowed", p.returncode == 0, p.stderr[-300:] if p.returncode else "")
        phase("staging-session", "staging")

        print("----- 2. PRODUCTION on the same database")
        p = run(flask + ["db", "upgrade"], "production")
        expect("production start step 1: flask db upgrade → exit 0, nothing to migrate",
               p.returncode == 0 and "Running upgrade" not in p.stderr, p.stderr[-300:] if p.returncode else "")
        p = run(flask + ["seed", "dev"], "production")
        expect("production start step 2 (only with SEED_DEMO_DATA=true): flask seed dev → REFUSED, the deploy would stop",
               p.returncode != 0 and "only allowed" in (p.stderr + p.stdout), f"exit {p.returncode}")
        phase("production", "production")

        print("----- 3. photo switch with the DRAFT consent version (configuration only)")
        photos = dict(PHOTO_UPLOADS_ENABLED="true", PHOTO_STORAGE_BACKEND="database", PHOTO_DB_QUOTA_BYTES="209715200",
                      PHOTO_CONSENT_APPROVED_VERSION=consent)
        phase("photo-gate", "staging", **photos)
        phase("photo-gate", "production", **photos)

        print("----- 4. ROLLBACK: STAGING again")
        phase("staging-again", "staging")
    finally:
        subprocess.run([exe("pg_ctl"), "-D", str(tmp / "data"), "-m", "fast", "-w", "stop"], **quiet)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{sum(results)}/{len(results)} checks passed — {version}, throw-away database, synthetic data")
    sys.exit(0 if results and all(results) else 1)


if __name__ == "__main__":
    phase_main() if PHASE else main()
