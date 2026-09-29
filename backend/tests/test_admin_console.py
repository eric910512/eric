"""Sprint 7: admin console — overview, accounts of every role, disable / enable (tokens stop at
once), unlock, admin creation, audit log search (itself audited), read-only settings, alert rules
(list / create / change / disable / dry run, engine uses the change), symptom form composition
(version +1), no clinical write access for admins, audit of every change."""
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

from app import create_app
from app.extensions import db
from app.models import AlertRule, AuditLog, Notification, PatientProfile, User
from app.models.base import utcnow
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()


def login(email, password="Demo@1234"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password})


def token(email, password="Demo@1234"):
    return login(email, password).get_json()["data"]["access_token"]


def H(t):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": str(uuid.uuid4())}


def err(r):
    j = r.get_json() or {}
    return r.status_code, j.get("error", {}).get("code"), [d.get("field") for d in j.get("error", {}).get("details", [])]


with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()
    at, nt, pt = token("admin01@demo.local"), token("nurse01@demo.local"), token("patient01@demo.local")
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    admin = db.session.query(User).filter_by(email="admin01@demo.local").one()
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()

    # ================================================================ access
    for path in ("/api/v1/admin/overview", "/api/v1/admin/users", "/api/v1/admin/audit-logs", "/api/v1/admin/settings",
                 "/api/v1/notifications/alert-rules", "/api/v1/symptoms/forms"):
        check(f"{path}: nurse and patient → 403", c.get(path, headers=H(nt)).status_code == 403 and c.get(path, headers=H(pt)).status_code == 403)

    # ================================================================ overview
    ov = c.get("/api/v1/admin/overview", headers=H(at)).get_json()["data"]
    check("overview numbers", ov["patients"]["total"] == 1 and ov["patients"]["unassigned"] == 0 and ov["nurses"]["total"] == 1
          and ov["assignments"]["active"] == 1 and ov["symptom_reviews"]["pending"] >= 1, ov)

    # ================================================================ accounts
    allu = c.get("/api/v1/admin/users?role=all", headers=H(at)).get_json()["data"]
    check("all accounts listed with role and status", {u["role"] for u in allu} == {"admin", "nurse", "patient"}
          and all({"is_active", "locked", "must_change_password", "last_login_at"} <= set(u) for u in allu))
    pat = next(u for u in allu if u["role"] == "patient")
    check("patient account shows its patient code", pat["patient"]["patient_code"] == "P00001")
    r = c.post("/api/v1/admin/users", json={"email": "admin02@demo.local", "display_name": "第二管理者", "role": "admin"}, headers=H(at))
    a2 = r.get_json()["data"]
    check("admin creates another admin (temporary password once)", r.status_code == 201 and a2["role"] == "admin" and a2["temporary_password"])
    check("patients are not created here → 400", "role" in err(c.post("/api/v1/admin/users", json={"email": "x@demo.local", "display_name": "x", "role": "patient"}, headers=H(at)))[2])

    # disable → tokens stop at once; enable again
    r = c.patch(f"/api/v1/admin/users/{nurse.public_id}", json={"is_active": False}, headers=H(at))
    check("disable a nurse", r.status_code == 200 and r.get_json()["data"]["is_active"] is False)
    check("disabled nurse: existing token rejected at once, sign-in refused", c.get("/api/v1/auth/me", headers=H(nt)).status_code == 401
          and login("nurse01@demo.local").status_code in (401, 403))
    c.patch(f"/api/v1/admin/users/{nurse.public_id}", json={"is_active": True}, headers=H(at))
    nt = token("nurse01@demo.local")
    check("enabled again: signs in and works", c.get("/api/v1/dashboard/widgets/caseload/data", headers=H(nt)).status_code == 200)
    check("cannot disable yourself → 409", err(c.patch(f"/api/v1/admin/users/{admin.public_id}", json={"is_active": False}, headers=H(at)))[:2] == (409, "CONFLICT"))
    check("role / email / password cannot be changed here → 400", set(err(c.patch(f"/api/v1/admin/users/{nurse.public_id}",
          json={"role": "admin", "email": "z@demo.local", "password": "x"}, headers=H(at)))[2]) >= {"role", "email", "password"})

    # lockout → unlock
    for _ in range(5):
        login("patient01@demo.local", "wrong-password")
    check("5 wrong passwords lock the account", login("patient01@demo.local").status_code == 423)
    locked = [u for u in c.get("/api/v1/admin/users?role=all&status=locked", headers=H(at)).get_json()["data"]]
    check("status=locked lists it", [u["email"] for u in locked] == ["patient01@demo.local"])
    r = c.patch(f"/api/v1/admin/users/{pat['id']}", json={"unlock": True}, headers=H(at))
    check("unlock → can sign in again", r.get_json()["data"]["locked"] is False and login("patient01@demo.local").status_code == 200)
    r = c.patch(f"/api/v1/admin/users/{nurse.public_id}", json={"display_name": "測試護理師 林（改）", "nurse_profile": {"department": "化療中心"}}, headers=H(at))
    check("edit nurse name / profile", r.get_json()["data"]["nurse_profile"]["department"] == "化療中心")

    # ================================================================ admins have no clinical write access
    check("admin cannot record vitals / labs / symptoms / assessments / medications",
          c.post("/api/v1/vital-signs", json={"patient_id": p1.public_id, "temperature_c": 37}, headers=H(at)).status_code == 403
          and c.post("/api/v1/labs/results", json={"patient_id": p1.public_id, "collected_at": "2026-09-01T00:00:00Z", "results": []}, headers=H(at)).status_code == 403
          and c.post("/api/v1/symptoms/records", json={"patient_id": p1.public_id}, headers=H(at)).status_code == 403
          and c.post("/api/v1/nursing-assessments", json={"patient_id": p1.public_id}, headers=H(at)).status_code == 403)

    # ================================================================ settings
    st = c.get("/api/v1/admin/settings", headers=H(at)).get_json()["data"]
    check("settings: institution + security policy, no secrets", st["institution"]["organization"]["name"] and st["security"]["login_lockout"]["max_failed_attempts"] == 5
          and "SECRET" not in json.dumps(st) and "jwt_secret" not in json.dumps(st).lower())

    # ================================================================ alert rules
    rules = c.get("/api/v1/notifications/alert-rules", headers=H(at)).get_json()["data"]
    fever = next(r for r in rules if r["code"] == "fever")
    check("rules list with targets", fever["source_type"] == "vital_sign" and fever["target"]["code"] == "temperature_c" and fever["threshold_value"] == 38.0)
    t = c.post(f"/api/v1/notifications/alert-rules/{fever['id']}/test", json={"value": 38.5, "in_nadir": False}, headers=H(at)).get_json()["data"]
    check("dry run: matches, nothing sent", t["matches"] is True and t["sent"] is False)
    before = db.session.query(Notification).count()
    t2 = c.post(f"/api/v1/notifications/alert-rules/{fever['id']}/test", json={"value": 37.0}, headers=H(at)).get_json()["data"]
    check("dry run below threshold: no match, no notification", t2["matches"] is False and db.session.query(Notification).count() == before)
    check("rule validation", set(err(c.patch(f"/api/v1/notifications/alert-rules/{fever['id']}", json={"operator": "~", "severity": "x", "cooldown_minutes": -1,
                                                                                                    "extra_conditions": {"foo": 1}}, headers=H(at)))[2])
          >= {"operator", "severity", "cooldown_minutes", "extra_conditions"})
    r = c.patch(f"/api/v1/notifications/alert-rules/{fever['id']}", json={"is_active": False}, headers=H(at))
    check("disable the fever rule", r.get_json()["data"]["is_active"] is False)
    v = c.post("/api/v1/vital-signs", json={"patient_id": "me", "temperature_c": 38.7}, headers=H(pt)).get_json()["data"]
    check("engine uses the change: a febrile reading raises no fever alert while disabled",
          all(a["alert_rule_code"] != "fever" for a in v["triggered_alerts"]))
    c.patch(f"/api/v1/notifications/alert-rules/{fever['id']}", json={"is_active": True}, headers=H(at))
    r = c.post("/api/v1/notifications/alert-rules", json={"code": "e2e_high_hr", "name": "心跳過快（測試規則）", "source_type": "vital_sign",
                                                          "vital_field": "heart_rate_bpm", "operator": ">=", "threshold_value": 140, "severity": "warning"}, headers=H(at))
    check("create a rule", r.status_code == 201 and r.get_json()["data"]["target"]["code"] == "heart_rate_bpm")
    check("duplicate code → 409", c.post("/api/v1/notifications/alert-rules", json={"code": "e2e_high_hr", "name": "x", "source_type": "vital_sign",
                                                                                     "vital_field": "heart_rate_bpm", "operator": ">=", "threshold_value": 1, "severity": "warning"}, headers=H(at)).status_code == 409)
    v2 = c.post("/api/v1/vital-signs", json={"patient_id": "me", "heart_rate_bpm": 145}, headers=H(pt)).get_json()["data"]
    check("the new rule is used by the engine", any(a["alert_rule_code"] == "e2e_high_hr" for a in v2["triggered_alerts"]))

    # ================================================================ symptom forms
    forms = c.get("/api/v1/symptoms/forms", headers=H(at)).get_json()["data"]
    f = next(x for x in forms if x["code"] == "daily_chemo_check")
    v0 = f["version"]
    order = [i["definition_code"] for i in f["items"]]
    new_items = [{"definition_code": code, "is_required": True} for code in reversed(order)]
    r = c.put("/api/v1/symptoms/forms/daily_chemo_check", json={"items": new_items}, headers=H(at))
    f2 = r.get_json()["data"]
    check("reorder the form → version +1", r.status_code == 200 and f2["version"] == v0 + 1 and [i["definition_code"] for i in f2["items"]] == list(reversed(order)))
    check("unchanged composition → same version", c.put("/api/v1/symptoms/forms/daily_chemo_check", json={"items": new_items}, headers=H(at)).get_json()["data"]["version"] == v0 + 1)
    check("unknown definition → 400", "items[0].definition_code" in err(c.put("/api/v1/symptoms/forms/daily_chemo_check", json={"items": [{"definition_code": "xx"}]}, headers=H(at)))[2])
    check("patients get the new form order", [i["definition"]["code"] for i in c.get("/api/v1/symptoms/forms/daily_chemo_check", headers=H(pt)).get_json()["data"]["items"]] == list(reversed(order)))
    check("nurse cannot change forms → 403", c.put("/api/v1/symptoms/forms/daily_chemo_check", json={"items": new_items}, headers=H(nt)).status_code == 403)

    # ================================================================ audit log search
    r = c.get("/api/v1/admin/audit-logs?resource_type=users&per_page=20", headers=H(at)).get_json()
    check("audit search by resource type", r["meta"]["total"] >= 3 and all(x["resource_type"] == "users" for x in r["data"]))
    r = c.get(f"/api/v1/admin/audit-logs?patient_id={p1.public_id}&action=CREATE", headers=H(at)).get_json()
    check("audit search by patient + action", r["data"] and all(x["patient"]["patient_code"] == "P00001" and x["action"] == "CREATE" for x in r["data"]))
    r = c.get(f"/api/v1/admin/audit-logs?actor_id={admin.public_id}", headers=H(at)).get_json()
    check("audit search by actor", r["data"] and all(x["actor"]["id"] == admin.public_id for x in r["data"]))
    check("audit search: bad date → 400", c.get("/api/v1/admin/audit-logs?from=2026-99-99", headers=H(at)).status_code == 400)
    check("the search itself is audited", db.session.query(AuditLog).filter_by(resource_type="audit_logs", action="VIEW").count() >= 3)
    blob = json.dumps([l.changes for l in db.session.query(AuditLog).all()], ensure_ascii=False)
    check("audit: account / rule / form changes recorded", "is_active" in blob and "e2e_high_hr" in blob and "symptom_forms" in {l.resource_type for l in db.session.query(AuditLog).all()})
    check("no password or temporary password in audit", a2["temporary_password"] not in blob and "Demo@1234" not in blob)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
