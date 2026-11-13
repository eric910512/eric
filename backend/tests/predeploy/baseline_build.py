"""Pre-deployment check, step 0 — build the SIMULATED production baseline with the CODE THAT IS DEPLOYED (the
chat release, 71e9d8e): run this file with that code's backend on the path (see the runner), against an
ISOLATED local PostgreSQL. Migrates to that code's head (b5cd01c11e1d), loads the demo seed, creates 50
training pairs through the admin API, sends chat messages, submits and reviews a daily report.

Usage: DATABASE_URL=postgresql://…@127.0.0.1:<port>/postgres BACKEND=<old backend dir> python baseline_build.py
"""
import os
import secrets
import sys
import uuid
import warnings
from urllib.parse import urlparse

url = os.environ.get("DATABASE_URL", "")
if urlparse(url).hostname not in ("127.0.0.1", "localhost") or not url.startswith("postgresql"):
    raise SystemExit("refusing: DATABASE_URL must be a local PostgreSQL")
sys.path.insert(0, os.environ["BACKEND"])
PASSWORD = "Predeploy123"
os.environ.update(FLASK_ENV="staging", SEED_DEMO_PASSWORD=PASSWORD, CORS_ORIGINS="https://predeploy.invalid")
os.environ.setdefault("SECRET_KEY", secrets.token_hex(32))
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_hex(32))

from flask_migrate import upgrade  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.seeds.dev import seed_dev_data  # noqa: E402

app = create_app("staging")
c = app.test_client()


def token(email):
    return c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).get_json()["data"]["access_token"]


def H(t):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": str(uuid.uuid4())}


with app.app_context(), warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    upgrade(directory=os.path.join(os.environ["BACKEND"], "migrations"))
    seed_dev_data(PASSWORD)
    db.session.commit()
    at = token("admin01@demo.local")
    for start in range(1, 51, 10):
        c.post("/api/v1/admin/training-accounts", json={
            "start": start, "count": 10, "patient_prefix": "trpt", "nurse_prefix": "trnu", "domain": "demo.local",
            "patient_name_prefix": "教學病人", "nurse_name_prefix": "教學護理師", "password": PASSWORD, "confirm": True}, headers=H(at))
    pt, nt, t1 = token("patient01@demo.local"), token("nurse01@demo.local"), token("trpt001@demo.local")
    c.post("/api/v1/patients/me/messages", json={"body": "baseline：病人訊息"}, headers=H(pt))
    c.post("/api/v1/patients/me/messages", json={"body": "baseline：教學病人訊息"}, headers=H(t1))
    pid1 = db.session.execute(text("SELECT public_id FROM patient_profiles WHERE patient_code = 'P00001'")).scalar()
    c.post(f"/api/v1/patients/{pid1}/messages", json={"body": "baseline：護理師回覆"}, headers=H(nt))
    daily = [{"definition_code": k, ("value_numeric" if isinstance(v, int) else "option_code"): v} for k, v in dict(
        rt_pain_skin=2, rt_pain_oral=1, rt_dermatitis_redness="light", rt_dermatitis_desquamation="dry", rt_appetite_poor=1, rt_fatigue=2,
        self_care_moisturizer="yes", self_care_towel_pat="yes", self_care_mouth_rinse="yes").items()]
    rec = c.post("/api/v1/symptoms/records", json={"patient_id": "me", "form_code": "rt_daily_report", "values": daily}, headers=H(pt)).get_json()["data"]["id"]
    c.post(f"/api/v1/symptoms/records/{rec}/review", json={"action_note": "baseline 審閱"}, headers=H(nt))
    q = lambda s: db.session.execute(text(s)).scalar()
    print("baseline built with the deployed code:", "revision", q("SELECT version_num FROM alembic_version"),
          "| training accounts", q("SELECT COUNT(*) FROM users WHERE email LIKE 'trpt%' OR email LIKE 'trnu%'"),
          "| chat messages", q("SELECT COUNT(*) FROM chat_messages"), "| symptom records", q("SELECT COUNT(*) FROM symptom_records"),
          "| nursing assessments", q("SELECT COUNT(*) FROM nursing_assessments"))
    db.session.remove()
    db.engine.dispose()
