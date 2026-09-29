"""Authentication hardening: server-side sessions — every access token belongs to a session
(auth_tokens family, hashed), logout ends it at once, own session list / end one / end others,
password change ends the other sessions, admin force sign-out, admin password reset (temporary
password, must change, sessions end), disabling ends sessions for good, audit without secrets."""
import sys
from pathlib import Path
import json
import uuid
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from flask_jwt_extended import create_access_token, decode_token

from app import create_app
from app.extensions import db
from app.models import AuditLog, AuthToken, User
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()


def login(email, password="Demo@1234", agent="E2E-Browser/1"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password}, headers={"User-Agent": agent})


def token(email, password="Demo@1234", agent="E2E-Browser/1"):
    return login(email, password, agent).get_json()["data"]["access_token"]


def H(t):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": str(uuid.uuid4())}


def me(t):
    return c.get("/api/v1/auth/me", headers=H(t)).status_code


with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()

    # ================================================================ login opens a session
    before = db.session.query(AuthToken).filter_by(user_id=nurse.id).count()
    a = token("nurse01@demo.local", agent="Laptop")
    claims = decode_token(a)
    rows = db.session.query(AuthToken).filter_by(user_id=nurse.id).all()
    check("login stores one session token (refresh family), hashed", len(rows) == before + 1 and rows[-1].token_type == "refresh"
          and len(rows[-1].token_hash) == 64 and rows[-1].family_id == claims["sid"] and rows[-1].user_agent == "Laptop")
    check("access token carries the session id (sid)", isinstance(claims.get("sid"), str) and me(a) == 200)
    check("login response unchanged (access_token, token_type, expires_in, user)",
          set(login("nurse01@demo.local").get_json()["data"]) == {"access_token", "token_type", "expires_in", "user"})
    with app.test_request_context():
        forged = create_access_token(identity=nurse.public_id, additional_claims={"role": "nurse"})
        forged_sid = create_access_token(identity=nurse.public_id, additional_claims={"role": "nurse", "sid": str(uuid.uuid4())})
    check("a token without a session, or with an unknown session → 401", me(forged) == 401 and me(forged_sid) == 401)

    # ================================================================ own sessions
    b = token("nurse01@demo.local", agent="Phone")
    lst = c.get("/api/v1/auth/sessions", headers=H(a)).get_json()["data"]
    cur = [s for s in lst if s["current"]]
    check("session list: every active sign-in, current one marked first", len(lst) >= 3 and len(cur) == 1 and lst[0]["current"] and cur[0]["id"] == claims["sid"], len(lst))
    check("session list fields: id, created_at, expires_at, ip, user agent (no token hash)",
          set(lst[0]) == {"id", "created_at", "expires_at", "ip_address", "user_agent", "current"} and "token_hash" not in json.dumps(lst))
    b_sid = decode_token(b)["sid"]
    check("end another own session → 204; that token stops at once", c.delete(f"/api/v1/auth/sessions/{b_sid}", headers=H(a)).status_code == 204 and me(b) == 401 and me(a) == 200)
    check("ending it again / an unknown session → 404", c.delete(f"/api/v1/auth/sessions/{b_sid}", headers=H(a)).status_code == 404
          and c.delete(f"/api/v1/auth/sessions/{uuid.uuid4()}", headers=H(a)).status_code == 404)
    p_t = token("patient01@demo.local")
    check("cannot end someone else's session → 404", c.delete(f"/api/v1/auth/sessions/{decode_token(p_t)['sid']}", headers=H(a)).status_code == 404 and me(p_t) == 200)
    x1, x2 = token("nurse01@demo.local"), token("nurse01@demo.local")
    r = c.post("/api/v1/auth/sessions/revoke-others", headers=H(a))
    check("sign out everywhere else → others end, this one keeps working", r.status_code == 200 and r.get_json()["data"]["revoked"] >= 2
          and me(x1) == 401 and me(x2) == 401 and me(a) == 200, r.get_json())
    check("only the current session remains", [s["current"] for s in c.get("/api/v1/auth/sessions", headers=H(a)).get_json()["data"]] == [True])

    # ================================================================ logout
    r = c.post("/api/v1/auth/logout", headers=H(a))
    check("logout → 204; the same access token is rejected right away", r.status_code == 204 and me(a) == 401)
    check("logout audited (LOGOUT, auth)", db.session.query(AuditLog).filter_by(action="LOGOUT", actor_user_id=nurse.id).count() == 1)
    # logout no longer needs a live access token (it must also work once the access token has
    # expired); a repeat with the dead token ends nothing and the token stays rejected
    r = c.post("/api/v1/auth/logout", headers=H(a))
    check("logout again with the dead token → 204, nothing more ended, token still rejected", r.status_code == 204 and me(a) == 401
          and db.session.query(AuditLog).filter_by(action="LOGOUT", actor_user_id=nurse.id).count() == 1)

    # ================================================================ password change policy
    keep, other = token("patient01@demo.local"), token("patient01@demo.local")
    r = c.put("/api/v1/auth/password", json={"current_password": "Demo@1234", "new_password": "NewPass2026"}, headers=H(keep))
    check("password change → 204: other sessions end, the current one continues", r.status_code == 204 and me(other) == 401 and me(p_t) == 401 and me(keep) == 200)
    log = db.session.query(AuditLog).filter_by(resource_type="users", action="UPDATE").order_by(AuditLog.id.desc()).first()
    check("password change audited with the number of sessions ended (no password)", log.changes.get("sessions_revoked") == 2 and log.changes.get("password") == "changed"
          and "NewPass2026" not in json.dumps(log.changes), log.changes)
    check("old password no longer works, new one does", login("patient01@demo.local").status_code == 401 and login("patient01@demo.local", "NewPass2026").status_code == 200)

    # ================================================================ admin: force sign-out, password reset, disable
    at = token("admin01@demo.local")
    n1, n2 = token("nurse01@demo.local"), token("nurse01@demo.local")
    users = c.get("/api/v1/admin/users?role=nurse", headers=H(at)).get_json()["data"]
    row = next(u for u in users if u["email"] == "nurse01@demo.local")
    check("admin account list shows active_sessions", row["active_sessions"] == 2, row.get("active_sessions"))
    ss = c.get(f"/api/v1/admin/users/{row['id']}/sessions", headers=H(at))
    check("admin sees the account's sessions", ss.status_code == 200 and len(ss.get_json()["data"]) == 2 and not any(s["current"] for s in ss.get_json()["data"]))
    r = c.post(f"/api/v1/admin/users/{row['id']}/revoke-sessions", headers=H(at))
    check("admin force sign-out → both tokens rejected; the account can sign in again", r.status_code == 200 and r.get_json()["data"]["revoked"] == 2
          and me(n1) == 401 and me(n2) == 401 and login("nurse01@demo.local").status_code == 200)
    admin = db.session.query(User).filter_by(email="admin01@demo.local").one()
    check("admin cannot force-sign-out or reset themselves → 409",
          c.post(f"/api/v1/admin/users/{admin.public_id}/revoke-sessions", headers=H(at)).status_code == 409
          and c.post(f"/api/v1/admin/users/{admin.public_id}/password-reset", headers=H(at)).status_code == 409)
    n3 = token("nurse01@demo.local")
    r = c.post(f"/api/v1/admin/users/{row['id']}/password-reset", headers=H(at))
    d = r.get_json()["data"]
    temp = d["temporary_password"]
    check("password reset → new temporary password once, must change, sessions ended", r.status_code == 200 and len(temp) == 14 and d["must_change_password"] is True
          and d["sessions_revoked"] >= 1 and me(n3) == 401)
    check("old password rejected, temporary password accepted (first-login flow)", login("nurse01@demo.local").status_code == 401
          and login("nurse01@demo.local", temp).status_code == 200
          and login("nurse01@demo.local", temp).get_json()["data"]["user"]["must_change_password"] is True)
    t_temp = token("nurse01@demo.local", temp)
    check("with the temporary password only /auth/me, password change and logout are allowed",
          c.get("/api/v1/patients", headers=H(t_temp)).status_code == 403 and me(t_temp) == 200
          and c.post("/api/v1/auth/logout", headers=H(t_temp)).status_code == 204 and me(t_temp) == 401)
    t_temp = token("nurse01@demo.local", temp)
    c.put("/api/v1/auth/password", json={"current_password": temp, "new_password": "Nurse2026ok"}, headers=H(t_temp))
    n4 = token("nurse01@demo.local", "Nurse2026ok")
    r = c.patch(f"/api/v1/admin/users/{row['id']}", json={"is_active": False}, headers=H(at))
    check("disable → sessions ended", r.status_code == 200 and me(n4) == 401 and r.get_json()["data"]["active_sessions"] == 0)
    c.patch(f"/api/v1/admin/users/{row['id']}", json={"is_active": True}, headers=H(at))
    check("re-enable does not bring old sessions back", me(n4) == 401 and login("nurse01@demo.local", "Nurse2026ok").status_code == 200)
    check("non-admin cannot use the admin session endpoints → 403",
          c.post(f"/api/v1/admin/users/{row['id']}/revoke-sessions", headers=H(token("nurse01@demo.local", "Nurse2026ok"))).status_code == 403)

    logs = db.session.query(AuditLog).filter(AuditLog.resource_type == "users").all()
    dump = json.dumps([l.changes for l in logs], ensure_ascii=False, default=str)
    check("reset / force sign-out audited (UPDATE users)", '"forced": true' in dump and '"password": "reset"' in dump)
    check("audit never holds a password or token", temp not in dump and "Nurse2026ok" not in dump and "NewPass2026" not in dump and "token_hash" not in dump)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
