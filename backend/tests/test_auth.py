import sys
from pathlib import Path
import warnings
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from flask_jwt_extended import create_access_token
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import AuditLog, NursePatientAssignment, PatientProfile, Role, User
from app.models.base import utcnow
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()


def login(email, pw="Demo@1234"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": pw})


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


with app.app_context():
    db.create_all()
    seed_dev_data()
    # extra fixtures: a second patient NOT assigned to the nurse, a second nurse, an admin
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    other_user = User(role=roles["patient"], email="patient02@demo.local", password_hash=pw, display_name="測試病人 乙", password_changed_at=utcnow())
    other = PatientProfile(patient_code="P00002", user=other_user, display_name="測試病人 乙", date_of_birth=date(1964, 1, 1))
    nurse2 = User(role=roles["nurse"], email="nurse02@demo.local", password_hash=pw, display_name="測試護理師 陳", password_changed_at=utcnow())
    admin = User(role=roles["admin"], email="admin@demo.local", password_hash=pw, display_name="管理者", password_changed_at=utcnow())
    inactive = User(role=roles["nurse"], email="inactive@demo.local", password_hash=pw, display_name="停用", is_active=False, password_changed_at=utcnow())
    db.session.add_all([other, nurse2, admin, inactive])
    db.session.commit()
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    P1, P2 = p1.public_id, other.public_id

    # ---------------- login ----------------
    r = login("patient01@demo.local")
    j = r.get_json()["data"]
    check("patient login 200 + Bearer token + user payload",
          r.status_code == 200 and j["token_type"] == "Bearer" and j["expires_in"] == 900
          and j["user"]["role"] == "patient" and j["user"]["patient_id"] == P1, j["user"])
    pt = j["access_token"]
    r = login("NURSE01@demo.local")
    check("email is case-insensitive; nurse role, patient_id null",
          r.status_code == 200 and r.get_json()["data"]["user"]["role"] == "nurse"
          and r.get_json()["data"]["user"]["patient_id"] is None)
    nt = r.get_json()["data"]["access_token"]
    nt2 = login("nurse02@demo.local").get_json()["data"]["access_token"]
    at = login("admin@demo.local").get_json()["data"]["access_token"]

    wrong = login("patient01@demo.local", "nope")
    unknown = login("ghost@demo.local", "nope")
    check("wrong password → 401 UNAUTHENTICATED", wrong.status_code == 401 and wrong.get_json()["error"]["code"] == "UNAUTHENTICATED")
    check("unknown email → identical 401 message (no account enumeration)",
          unknown.status_code == 401 and unknown.get_json()["error"]["message"] == wrong.get_json()["error"]["message"])
    check("inactive account → 401", login("inactive@demo.local").status_code == 401)
    bad = c.post("/api/v1/auth/login", json={"email": ""})
    check("missing fields → 400 VALIDATION_ERROR with details",
          bad.status_code == 400 and {d["field"] for d in bad.get_json()["error"]["details"]} == {"email", "password"})
    check("non-JSON body → 400", c.post("/api/v1/auth/login", data="x").status_code == 400)

    # lockout: 5 failures (1 already above) → locked
    for _ in range(4):
        login("patient01@demo.local", "nope")
    locked = login("patient01@demo.local")  # correct password, but locked
    check("5 failures → 423 ACCOUNT_LOCKED even with correct password",
          locked.status_code == 423 and locked.get_json()["error"]["code"] == "ACCOUNT_LOCKED",
          locked.get_json()["error"]["details"])
    u = db.session.query(User).filter_by(email="patient01@demo.local").one()
    u.locked_until = None
    db.session.commit()
    check("after lock expires, login works and counter resets",
          login("patient01@demo.local").status_code == 200 and u.failed_login_count == 0)

    # ---------------- /me ----------------
    me = c.get("/api/v1/auth/me", headers=bearer(nt)).get_json()["data"]
    check("/auth/me returns nurse profile", me["role"] == "nurse" and me["nurse_profile"]["staff_code"] == "N0001")

    # ---------------- dashboard protection ----------------
    url = lambda pid: f"/api/v1/dashboard/patient/{pid}"
    r = c.get(url(P1))
    check("no token → 401 UNAUTHENTICATED (envelope)",
          r.status_code == 401 and r.get_json()["error"]["code"] == "UNAUTHENTICATED" and r.get_json()["error"]["request_id"])
    check("garbage token → 401", c.get(url(P1), headers=bearer("abc.def.ghi")).status_code == 401)
    expired = create_access_token(identity=u.public_id, expires_delta=timedelta(seconds=-10))
    r = c.get(url(P1), headers=bearer(expired))
    check("expired token → 401 TOKEN_EXPIRED", r.status_code == 401 and r.get_json()["error"]["code"] == "TOKEN_EXPIRED")

    check("patient → own dashboard 200", c.get(url(P1), headers=bearer(pt)).status_code == 200)
    check("patient → 'me' 200", c.get(url("me"), headers=bearer(pt)).get_json()["data"]["patient_id"] == P1)
    check("patient → other patient 404 (not 403)", c.get(url(P2), headers=bearer(pt)).status_code == 404)
    check("nurse → assigned patient 200", c.get(url(P1), headers=bearer(nt)).status_code == 200)
    check("nurse → unassigned patient 404", c.get(url(P2), headers=bearer(nt)).status_code == 404)
    check("nurse → 'me' 404", c.get(url("me"), headers=bearer(nt)).status_code == 404)
    check("admin → any patient 200", c.get(url(P2), headers=bearer(at)).status_code == 200)

    # deactivated after token issued → token stops working
    n2 = db.session.query(User).filter_by(email="nurse02@demo.local").one()
    n2.is_active = False
    db.session.commit()
    check("user deactivated after login → 401", c.get("/api/v1/auth/me", headers=bearer(nt2)).status_code == 401)

    # ended assignment → access revoked
    asg = db.session.query(NursePatientAssignment).filter_by(patient_id=p1.id).one()
    from app.models.base import utcnow
    asg.ended_at = utcnow()
    db.session.commit()
    check("nurse assignment ended → 404", c.get(url(P1), headers=bearer(nt)).status_code == 404)
    asg.ended_at = None
    db.session.commit()

    # ---------------- caseload ----------------
    r = c.get("/api/v1/dashboard/widgets/caseload/data", headers=bearer(nt))
    body = r.get_json()
    check("nurse caseload 200, only assigned patients",
          r.status_code == 200 and [i["patient_code"] for i in body["data"]] == ["P00001"] and body["meta"]["total"] == 1,
          {k: body["data"][0][k] for k in ("risk_level", "cycle", "pending_review_count", "care_alert_types")})
    r = c.get("/api/v1/dashboard/widgets/caseload/data", headers=bearer(pt))
    check("patient → caseload 403 FORBIDDEN", r.status_code == 403 and r.get_json()["error"]["code"] == "FORBIDDEN")

    # ---------------- audit ----------------
    logs = db.session.query(AuditLog).all()
    fails = [a for a in logs if a.action == "LOGIN_FAILED"]
    check("LOGIN / LOGIN_FAILED audited with reasons",
          any(a.action == "LOGIN" for a in logs)
          and {"bad_password", "unknown_email", "inactive", "locked", "bad_password_locked"} <= {a.reason for a in fails})
    check("password never stored in audit", not any("Demo@1234" in str(a.changes or "") + str(a.reason or "") for a in logs))
    views = [a for a in logs if a.action == "VIEW"]
    check("dashboard VIEW audited with real actor", views and all(a.actor_user_id and a.actor_role for a in views))

# production / staging refuse unsafe settings — everything is injected here, independent of the local .env
import os
from unittest.mock import patch

from app.config import DevelopmentConfig, ProductionConfig, StagingConfig, _database_url

STRONG = "x7Qp2vL9sT4mN8rW1zK6yB3hF5jD0cGa"  # 32 random-looking chars, test-only
GOOD = {"SECRET_KEY": STRONG, "JWT_SECRET_KEY": STRONG + "jwt", "DEBUG": False,
        "SQLALCHEMY_DATABASE_URI": "postgresql://user:pass@db.internal:5432/app", "CORS_ORIGINS": ["https://app.example.test"]}


def attempt(config_name="production", config_cls=ProductionConfig, env=None, **overrides):
    """(started, error message) for create_app() with a complete, valid setup plus overrides."""
    attrs = {**GOOD, **overrides}
    environ = {"JWT_SECRET_KEY": attrs["JWT_SECRET_KEY"], **(env or {})}
    patches = [patch.object(config_cls, k, v) for k, v in attrs.items()]
    with patch.dict(os.environ, environ):
        if "FLASK_DEBUG" not in (env or {}):
            os.environ.pop("FLASK_DEBUG", None)
        for pt in patches:
            pt.start()
        try:
            create_app(config_name)
            return True, ""
        except RuntimeError as e:
            return False, str(e)
        finally:
            for pt in patches:
                pt.stop()


def refused(**kw):
    return not attempt(**kw)[0]


check("production starts with strong secrets, PostgreSQL and explicit CORS (positive control)", attempt()[0], attempt()[1])
check("staging starts with the same settings", attempt("staging", StagingConfig)[0], attempt("staging", StagingConfig)[1])
check("production refuses the default SECRET_KEY ('dev')", refused(SECRET_KEY="dev"))
check("production refuses the .env.example SECRET_KEY placeholder", refused(SECRET_KEY="change-me"))
check("production refuses the .env.example JWT_SECRET_KEY placeholder",
      refused(JWT_SECRET_KEY="change-me-to-a-random-string-of-at-least-32-bytes"))
check("production refuses JWT_SECRET_KEY falling back to a weak SECRET_KEY", refused(SECRET_KEY="dev", JWT_SECRET_KEY="dev"))
check("production refuses secrets shorter than 32 characters", refused(SECRET_KEY=STRONG[:31]) and refused(JWT_SECRET_KEY=STRONG[:31]))
check("production refuses JWT_SECRET_KEY missing from the environment", refused(env={"JWT_SECRET_KEY": ""}))
check("production refuses JWT_SECRET_KEY equal to SECRET_KEY", refused(JWT_SECRET_KEY=STRONG))
check("production refuses DEBUG (config or FLASK_DEBUG=1)", refused(DEBUG=True) and refused(env={"FLASK_DEBUG": "1"}))
check("production refuses SQLite", refused(SQLALCHEMY_DATABASE_URI="sqlite:///app.db"))
check("production refuses missing / wildcard / plain-http CORS origins",
      refused(CORS_ORIGINS=[]) and refused(CORS_ORIGINS=["*"]) and refused(CORS_ORIGINS=["http://app.example.test"]))
check("staging applies the same checks", refused(config_name="staging", config_cls=StagingConfig, SECRET_KEY="dev"))
ok_, msg = attempt(SECRET_KEY="weak-secret-value-abc", JWT_SECRET_KEY="short-jwt")
check("refusal message never contains the secret values", not ok_ and "weak-secret-value-abc" not in msg and "short-jwt" not in msg, msg[:120])
check("development is not blocked by the default secrets",
      attempt("development", DevelopmentConfig, SECRET_KEY="dev", JWT_SECRET_KEY="dev", SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", CORS_ORIGINS=[])[0])
check("demo seed allowed in staging, not in production", StagingConfig.ALLOW_DEMO_SEED is True and ProductionConfig.ALLOW_DEMO_SEED is False)
with patch.dict(os.environ, {"DATABASE_URL": "postgres://u:p@h:5432/d"}):
    check("DATABASE_URL 'postgres://' is normalized to 'postgresql://'", _database_url() == "postgresql://u:p@h:5432/d")

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
