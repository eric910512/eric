"""Refresh token regression: the refresh token lives only in an HttpOnly + Secure + SameSite=Strict
cookie (Path=/api/v1/auth, host-only) — never in a response body; refresh rotates it, keeps the
session (sid) and absolute expiry, detects replay; logout / password change / admin revoke /
disable / expiry end refreshing; sessions of different devices are independent."""
import sys
from pathlib import Path
import json
import uuid
import warnings
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from flask_jwt_extended import create_access_token, decode_token

from app import create_app
from app.extensions import db
from app.models import AuditLog, AuthToken, User
from app.models.base import utcnow
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
BASE = "https://app.example.test"  # the cookie is Secure: the browser (and this client) only send it over https
XHR = {"X-Requested-With": "XMLHttpRequest"}


def device():
    """A separate browser: its own cookie jar."""
    return app.test_client()


def login(c, email="nurse01@demo.local", password="Demo@1234"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password}, base_url=BASE)


def refresh(c, headers=XHR):
    return c.post("/api/v1/auth/refresh", headers=headers, base_url=BASE)


def cookie(c):
    ck = c.get_cookie("refresh_token", domain="app.example.test", path="/api/v1/auth")
    return ck.value if ck else None


def set_cookie_header(r):
    return next((h for h in r.headers.getlist("Set-Cookie") if h.startswith("refresh_token=")), "")


def me(c, token):
    return c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}, base_url=BASE).status_code


def H(t):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": str(uuid.uuid4())}


with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()

    # ================================================================ 1. login → access token + cookie
    a = device()
    r = login(a)
    body = r.get_json()["data"]
    sc = set_cookie_header(r)
    attrs = {p.strip().split("=")[0].lower() for p in sc.split(";")[1:]}
    check("login: access token in the body, refresh token NOT in the body", "access_token" in body and "refresh_token" not in body
          and cookie(a) is not None and cookie(a) not in json.dumps(r.get_json()))
    check("cookie attributes: HttpOnly, Secure, SameSite=Strict, Path=/api/v1/auth, no Domain, Expires",
          {"httponly", "secure", "samesite", "path", "expires"} <= attrs and "samesite=strict" in sc.lower()
          and "path=/api/v1/auth" in sc.lower() and "domain" not in attrs, sc)
    stored = db.session.query(AuthToken).filter_by(family_id=decode_token(body["access_token"])["sid"]).one()
    check("only the hash is stored", stored.token_hash != cookie(a) and len(stored.token_hash) == 64)
    first_cookie, sid = cookie(a), decode_token(body["access_token"])["sid"]

    # ================================================================ 2–4. access token expires → refresh → API works
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    with app.test_request_context():
        expired = create_access_token(identity=nurse.public_id, additional_claims={"role": "nurse", "sid": sid}, expires_delta=timedelta(seconds=-5))
    r = a.get("/api/v1/patients", headers={"Authorization": f"Bearer {expired}"}, base_url=BASE)
    check("expired access token → 401 TOKEN_EXPIRED", r.status_code == 401 and r.get_json()["error"]["code"] == "TOKEN_EXPIRED")
    check("refresh without X-Requested-With → 403 (CSRF defence)", refresh(a, headers={}).status_code == 403 and cookie(a) == first_cookie)
    r = refresh(a)
    nb = r.get_json()["data"]
    check("refresh → 200 new access token, same session, body without refresh token", r.status_code == 200 and nb["access_token"] != body["access_token"]
          and decode_token(nb["access_token"])["sid"] == sid and "refresh_token" not in nb and nb["user"]["email"] == "nurse01@demo.local")
    check("refresh rotates the cookie", cookie(a) not in (None, first_cookie))
    check("the API request succeeds with the new access token", a.get("/api/v1/patients", headers={"Authorization": f"Bearer {nb['access_token']}"}, base_url=BASE).status_code == 200)
    rows = db.session.query(AuthToken).filter_by(family_id=sid).order_by(AuthToken.id).all()
    check("rotation: old row used, new row same family and same absolute expiry", len(rows) == 2 and rows[0].used_at is not None
          and rows[1].used_at is None and rows[1].expires_at == rows[0].expires_at)
    r = refresh(a)
    check("refreshing again with the rotated cookie works", r.status_code == 200)
    token_a = r.get_json()["data"]["access_token"]

    # ================================================================ replay of an old refresh token
    thief = device()
    thief.set_cookie("refresh_token", first_cookie, domain="app.example.test", path="/api/v1/auth")
    r = refresh(thief)
    check("replaying a used refresh token → 401 REFRESH_TOKEN_REUSED", r.status_code == 401 and r.get_json()["error"]["code"] == "REFRESH_TOKEN_REUSED")
    check("replay ends the whole session: the legitimate device can no longer refresh or call the API",
          refresh(a).status_code == 401 and me(a, token_a) == 401)
    check("replay audited", db.session.query(AuditLog).filter_by(reason="refresh_token_reused").count() == 1)
    check("a refused refresh clears the cookie", "refresh_token=;" in set_cookie_header(refresh(a)).replace('""', ""))

    # ================================================================ 5–6. logout → the refresh token cannot be used again
    b = device()
    tb = login(b).get_json()["data"]["access_token"]
    kept = cookie(b)
    r = b.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {tb}"}, base_url=BASE)
    check("logout → 204, cookie cleared", r.status_code == 204 and cookie(b) is None)
    b.set_cookie("refresh_token", kept, domain="app.example.test", path="/api/v1/auth")
    r = refresh(b)
    check("after logout the old refresh token cannot refresh → 401 REFRESH_TOKEN_REVOKED", r.status_code == 401 and r.get_json()["error"]["code"] == "REFRESH_TOKEN_REVOKED")
    check("after logout the access token is rejected", me(b, tb) == 401)

    c2 = device()
    t2 = login(c2).get_json()["data"]["access_token"]
    s2 = decode_token(t2)["sid"]
    with app.test_request_context():
        exp2 = create_access_token(identity=nurse.public_id, additional_claims={"role": "nurse", "sid": s2}, expires_delta=timedelta(seconds=-5))
    r = c2.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {exp2}"}, base_url=BASE)
    check("logout with an expired access token still ends the session", r.status_code == 204 and refresh(c2).status_code == 401)
    c3 = device()
    login(c3)
    r = c3.post("/api/v1/auth/logout", base_url=BASE)
    check("logout with only the cookie ends that session", r.status_code == 204 and refresh(c3).status_code == 401)
    with app.test_request_context():
        forged = create_access_token(identity=nurse.public_id, additional_claims={"sid": str(uuid.uuid4())})
    check("logout with nothing valid → 204, ends nothing", device().post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {forged}x"}, base_url=BASE).status_code == 204)

    # ================================================================ 7. password change → the other sessions cannot refresh
    phone, laptop = device(), device()
    tp = login(phone, "patient01@demo.local").get_json()["data"]["access_token"]
    tl = login(laptop, "patient01@demo.local").get_json()["data"]["access_token"]
    r = phone.put("/api/v1/auth/password", json={"current_password": "Demo@1234", "new_password": "Refresh2026ok"}, headers={"Authorization": f"Bearer {tp}"}, base_url=BASE)
    check("password change on the phone", r.status_code == 204)
    check("the laptop session can no longer refresh or call the API", refresh(laptop).status_code == 401 and me(laptop, tl) == 401)
    check("the phone (where the password was changed) keeps refreshing", refresh(phone).status_code == 200)

    # ================================================================ 8. admin revoke → cannot refresh
    at = login(device(), "admin01@demo.local").get_json()["data"]["access_token"]
    n1, n2 = device(), device()
    login(n1)
    login(n2)
    r = n1.post("/api/v1/admin/users/" + nurse.public_id + "/revoke-sessions", headers=H(at), base_url=BASE)
    check("admin force sign-out → every nurse session cannot refresh", r.status_code == 200 and refresh(n1).status_code == 401 and refresh(n2).status_code == 401)
    n3 = device()
    login(n3)
    lst = device().get(f"/api/v1/admin/users/{nurse.public_id}/sessions", headers=H(at), base_url=BASE).get_json()["data"]
    check("admin reset password → cannot refresh", device().post(f"/api/v1/admin/users/{nurse.public_id}/password-reset", headers=H(at), base_url=BASE).status_code == 200
          and refresh(n3).status_code == 401 and len(lst) == 1)

    # ================================================================ 9–10. one device / other devices; independence
    x, y, z = device(), device(), device()
    tx = login(x, "admin01@demo.local").get_json()["data"]["access_token"]
    ty = login(y, "admin01@demo.local").get_json()["data"]["access_token"]
    login(z, "admin01@demo.local")
    r = x.delete(f"/api/v1/auth/sessions/{decode_token(ty)['sid']}", headers=H(tx), base_url=BASE)
    check("ending one device: that device cannot refresh, the others can", r.status_code == 204 and refresh(y).status_code == 401
          and refresh(z).status_code == 200 and refresh(x).status_code == 200)
    tx = refresh(x).get_json()["data"]["access_token"]
    r = x.post("/api/v1/auth/sessions/revoke-others", headers=H(tx), base_url=BASE)
    check("sign out other devices: they cannot refresh, this one can", r.status_code == 200 and refresh(z).status_code == 401 and refresh(x).status_code == 200)
    check("each device has its own session id", len({decode_token(t)["sid"] for t in (tx, ty)}) == 2)

    # ================================================================ expiry / disabled / temporary password
    e = device()
    te = login(e, "admin01@demo.local").get_json()["data"]["access_token"]
    for row in db.session.query(AuthToken).filter_by(family_id=decode_token(te)["sid"]).all():
        row.expires_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    r = refresh(e)
    check("refresh token expired → 401 REFRESH_TOKEN_EXPIRED (back to sign-in)", r.status_code == 401 and r.get_json()["error"]["code"] == "REFRESH_TOKEN_EXPIRED")
    check("no cookie at all → 401 REFRESH_TOKEN_MISSING", refresh(device()).get_json()["error"]["code"] == "REFRESH_TOKEN_MISSING")
    patient = db.session.query(User).filter_by(email="patient01@demo.local").one()
    d = device()
    login(d, "patient01@demo.local", "Refresh2026ok")
    at = refresh(x).get_json()["data"]["access_token"]
    x.patch(f"/api/v1/admin/users/{patient.public_id}", json={"is_active": False}, headers=H(at), base_url=BASE)
    check("disabled account cannot refresh", refresh(d).status_code == 401)
    x.patch(f"/api/v1/admin/users/{patient.public_id}", json={"is_active": True}, headers=H(at), base_url=BASE)
    temp = x.post(f"/api/v1/admin/users/{nurse.public_id}/password-reset", headers=H(at), base_url=BASE).get_json()["data"]["temporary_password"]
    t = device()
    login(t, "nurse01@demo.local", temp)
    r = refresh(t)
    check("with a temporary password: refresh works but the API still requires the password change",
          r.status_code == 200 and r.get_json()["data"]["user"]["must_change_password"] is True
          and t.get("/api/v1/patients", headers={"Authorization": f"Bearer {r.get_json()['data']['access_token']}"}, base_url=BASE).status_code == 403)

    dump = json.dumps([row.changes for row in db.session.query(AuditLog).all()], ensure_ascii=False, default=str)
    check("audit never holds a refresh token", not any(v and v in dump for v in (first_cookie, kept)))

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
