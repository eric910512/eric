"""Training (demo) accounts: admin-only, development / staging only (production refuses), preview creates
nothing, 50 one-to-one pairs in batches of 10, shared password stored only as hashes and never echoed or
audited, no forced password change for training accounts (normal accounts unchanged), re-runs never reset
existing passwords, conflicts and failures leave no partial pair, strict nurse / patient isolation, and the
existing accounts / patients / assignments are untouched."""
import sys
from pathlib import Path
import json
import uuid
import warnings
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from werkzeug.security import check_password_hash

from app import create_app
from app.config import DevelopmentConfig, ProductionConfig, StagingConfig, TestingConfig
from app.extensions import db
from app.models import AuditLog, NursePatientAssignment, PatientProfile, User
from app.modules.admin import training
from app.modules.patient import management as m
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()
PASSWORD = "Train2026x"  # test-only value (never a real account password)
SPEC = {"patient_prefix": "patientfyu", "nurse_prefix": "nursefyu", "domain": "demo.local",
        "patient_name_prefix": "學生病人", "nurse_name_prefix": "學生護理師"}
PREVIEW = "/api/v1/admin/training-accounts/preview"
CREATE = "/api/v1/admin/training-accounts"


def token(email, password="Demo@1234"):
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    return r.get_json()["data"]["access_token"] if r.status_code == 200 else None


def H(t):
    return {"Authorization": f"Bearer {t}"}


def counts():
    return (db.session.query(User).count(), db.session.query(PatientProfile).count(),
            db.session.query(NursePatientAssignment).count())


def snapshot():
    users = {u.email: (u.password_hash, u.password_changed_at, u.is_active, u.display_name, u.role_name)
             for u in db.session.query(User).filter(User.email.in_(
                 ["admin01@demo.local", "nurse01@demo.local", "patient01@demo.local", "patient02@demo.local"]))}
    patients = {}
    for p in db.session.query(PatientProfile).filter(PatientProfile.patient_code.in_(["P00001", "P00002"])):
        rows = db.session.query(NursePatientAssignment).filter_by(patient_id=p.id).order_by(NursePatientAssignment.id).all()
        patients[p.patient_code] = (p.display_name, p.user_id, p.updated_at,
                                    [(a.id, a.nurse_id, a.is_primary, a.ended_at) for a in rows])
    return users, patients


with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()
    at = token("admin01@demo.local")
    # production-like starting point: P00002 / patient02 created through the admin flow
    r = c.post("/api/v1/patients", json={"display_name": "測試病人乙", "gender": "female", "date_of_birth": "1964-02-18",
                                         "account": {"email": "patient02@demo.local"}}, headers=H(at))
    p2_temp = r.get_json()["data"]["account"]["temporary_password"]
    t = token("patient02@demo.local", p2_temp)
    c.put("/api/v1/auth/password", json={"current_password": p2_temp, "new_password": "Patient2Pass88"}, headers=H(t))
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    p2 = db.session.query(PatientProfile).filter_by(patient_code="P00002").one()
    before = snapshot()
    nt, pt = token("nurse01@demo.local"), token("patient01@demo.local")

    # ================================================================ authorization / environment
    check("unauthenticated → 401", c.post(PREVIEW, json={**SPEC, "start": 1, "count": 1}).status_code == 401)
    for who, tok in (("nurse", nt), ("patient", pt)):
        check(f"{who} → 403 on preview and create", c.post(PREVIEW, json={**SPEC, "start": 1, "count": 1}, headers=H(tok)).status_code == 403
              and c.post(CREATE, json={**SPEC, "start": 1, "count": 1, "password": PASSWORD, "confirm": True}, headers=H(tok)).status_code == 403)
    check("allowed only where demo data is allowed: production off, staging / development / testing on",
          ProductionConfig.ALLOW_DEMO_SEED is False and StagingConfig.ALLOW_DEMO_SEED is True
          and DevelopmentConfig.ALLOW_DEMO_SEED is True and TestingConfig.ALLOW_DEMO_SEED is True)
    app.config["ALLOW_DEMO_SEED"] = False  # what ProductionConfig sets
    n0 = counts()
    r1 = c.post(PREVIEW, json={**SPEC, "start": 1, "count": 1}, headers=H(at))
    r2 = c.post(CREATE, json={**SPEC, "start": 1, "count": 1, "password": PASSWORD, "confirm": True}, headers=H(at))
    check("production: admin gets 403 TRAINING_ACCOUNTS_DISABLED, nothing created", r1.status_code == 403 and r2.status_code == 403
          and r2.get_json()["error"]["code"] == "TRAINING_ACCOUNTS_DISABLED" and counts() == n0)
    app.config["ALLOW_DEMO_SEED"] = True

    # ================================================================ validation
    def bad(body, field, url=CREATE):
        r = c.post(url, json=body, headers=H(at))
        fields = [d["field"] for d in (r.get_json() or {}).get("error", {}).get("details", [])]
        return r.status_code == 400 and field in fields
    full = {**SPEC, "start": 1, "count": 10, "password": PASSWORD, "confirm": True}
    check("create: more than 10 per request → 400", bad({**full, "count": 11}, "count"))
    check("preview: more than 50 → 400", bad({**SPEC, "start": 1, "count": 51}, "count", PREVIEW))
    check("password fyu0000 (7 characters) rejected by the normal policy", bad({**full, "password": "fyu0000"}, "password"))
    check("password without digits rejected", bad({**full, "password": "abcdefgh"}, "password"))
    check("confirm required", bad({**full, "confirm": False}, "confirm"))
    check("prefix must be lowercase letters / digits", bad({**full, "patient_prefix": "Patient fyu"}, "patient_prefix"))
    check("patient and nurse prefixes must differ", bad({**full, "nurse_prefix": "patientfyu"}, "nurse_prefix"))
    check("unknown field rejected (e.g. role)", bad({**full, "role": "admin"}, "role"))
    check("password not allowed on preview", bad({**SPEC, "start": 1, "count": 1, "password": PASSWORD}, "password", PREVIEW))
    check("numbers stay within 999", bad({**SPEC, "start": 990, "count": 20}, "count", PREVIEW))
    check("invalid requests created nothing", counts() == n0)

    # ================================================================ preview creates nothing
    audit0 = db.session.query(AuditLog).count()
    r = c.post(PREVIEW, json={**SPEC, "start": 1, "count": 50}, headers=H(at))
    pv = r.get_json()["data"]
    check("preview 50 → 200, all will_create, accounts / names as planned", r.status_code == 200 and pv["summary"] == {"will_create": 50, "exists": 0, "conflict": 0}
          and pv["rows"][0]["patient_email"] == "patientfyu001@demo.local" and pv["rows"][0]["nurse_email"] == "nursefyu001@demo.local"
          and pv["rows"][49]["patient_name"] == "學生病人 050" and pv["rows"][49]["nurse_name"] == "學生護理師 050", pv["summary"])
    check("preview created nothing (users, patients, assignments, audit)", counts() == n0 and db.session.query(AuditLog).count() == audit0)

    # ================================================================ create 50 in 5 requests of 10
    rows, bodies = [], []
    for start in range(1, 51, 10):
        r = c.post(CREATE, json={**SPEC, "start": start, "count": 10, "password": PASSWORD, "confirm": True}, headers=H(at))
        bodies.append(r.get_data(as_text=True))
        rows += r.get_json()["data"]["rows"] if r.status_code == 201 else []
    check("5 × 10 → 50 created", len(rows) == 50 and all(x["status"] == "created" for x in rows), [x["status"] for x in rows if x["status"] != "created"])
    codes = [x["patient_code"] for x in rows]
    check("patient codes P00003–P00052, unique", codes == [f"P{n:05d}" for n in range(3, 53)], codes[:3] + codes[-2:])
    check("password never in a response", all(PASSWORD not in b for b in bodies))
    check("+100 accounts, +50 patients, +50 assignments", counts() == (n0[0] + 100, n0[1] + 50, n0[2] + 50), (counts(), n0))
    pair_ok = True
    for n in range(1, 51):
        nn = f"{n:03d}"
        nurse = db.session.query(User).filter_by(email=f"nursefyu{nn}@demo.local").one()
        acct = db.session.query(User).filter_by(email=f"patientfyu{nn}@demo.local").one()
        pat = acct.patient_profile
        act = db.session.query(NursePatientAssignment).filter_by(patient_id=pat.id, ended_at=None).all()
        nurse_act = db.session.query(NursePatientAssignment).filter_by(nurse_id=nurse.id, ended_at=None).all()
        pair_ok &= (pat.display_name == f"學生病人 {nn}" and nurse.display_name == f"學生護理師 {nn}" and nurse.role_name == "nurse"
                    and acct.role_name == "patient" and len(act) == 1 and act[0].nurse_id == nurse.id and act[0].is_primary
                    and len(nurse_act) == 1 and nurse_act[0].patient_id == pat.id
                    and nurse.password_changed_at is not None and acct.password_changed_at is not None
                    and check_password_hash(nurse.password_hash, PASSWORD) and check_password_hash(acct.password_hash, PASSWORD)
                    and nurse.password_hash != PASSWORD and nurse.nurse_profile.staff_code == f"NURSEFYU{nn}")
    check("every pair: names, roles, exactly one primary assignment each way, hashed password, no forced change", pair_ok)
    check("no training patient assigned to nurse01, nurse01's assignments unchanged",
          db.session.query(NursePatientAssignment).join(User, User.id == NursePatientAssignment.nurse_id)
          .filter(User.email == "nurse01@demo.local").count() == 1)

    # ================================================================ sign-in: no forced change
    n1, p1t = token("nursefyu001@demo.local", PASSWORD), token("patientfyu001@demo.local", PASSWORD)
    me = c.get("/api/v1/auth/me", headers=H(p1t)).get_json()["data"]
    check("training accounts sign in with the shared password, no forced change", n1 and p1t and me["must_change_password"] is False)
    check("…and can use the API straight away (not 403 PASSWORD_CHANGE_REQUIRED)", c.get("/api/v1/patients/me", headers=H(p1t)).status_code == 200)

    # ================================================================ isolation
    pid = {n: db.session.query(User).filter_by(email=f"patientfyu{n:03d}@demo.local").one().patient_profile.public_id for n in (1, 2, 50)}
    lst = c.get("/api/v1/patients?per_page=100", headers=H(n1)).get_json()
    check("nursefyu001 lists only 學生病人 001", lst["meta"]["total"] == 1 and lst["data"][0]["display_name"] == "學生病人 001", lst["meta"])
    check("nursefyu001 reads its patient (profile, symptom reports, dashboard)", all(c.get(u, headers=H(n1)).status_code == 200 for u in (
        f"/api/v1/patients/{pid[1]}", f"/api/v1/symptoms/records/{pid[1]}", f"/api/v1/dashboard/patient/{pid[1]}")))
    blocked = {(who, target, u): c.get(u.format(target), headers=H(tok)).status_code
               for who, tok in (("nurse001", n1), ("patient001", p1t))
               for target in (pid[2], pid[50], p1.public_id, p2.public_id)
               for u in ("/api/v1/patients/{}", "/api/v1/symptoms/records/{}", "/api/v1/dashboard/patient/{}")}
    check("nursefyu001 / patientfyu001 → patientfyu002, 050, P00001, P00002: 404 everywhere", all(v == 404 for v in blocked.values()),
          {k: v for k, v in blocked.items() if v != 404})
    n2 = token("nursefyu002@demo.local", PASSWORD)
    check("nursefyu002 cannot see patientfyu001", c.get(f"/api/v1/patients/{pid[1]}", headers=H(n2)).status_code == 404)
    check("a training patient cannot list patients (403)", c.get("/api/v1/patients", headers=H(p1t)).status_code == 403)
    check("nurse01 cannot see training patients", c.get(f"/api/v1/patients/{pid[1]}", headers=H(nt)).status_code == 404)

    # ================================================================ re-run: nothing reset
    c.put("/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": "MyOwnPass99"}, headers=H(p1t))
    n_before = counts()
    r = c.post(CREATE, json={**SPEC, "start": 1, "count": 10, "password": "Different123", "confirm": True}, headers=H(at))
    d = r.get_json()["data"]
    check("re-run 001–010 → all exists, nothing created", r.status_code == 201 and d["summary"]["exists"] == 10 and counts() == n_before
          and d["rows"][0]["patient_code"] == "P00003" and d["rows"][0]["primary_assignment"] is True, d["summary"])
    check("re-run never resets passwords (own new password still works; batch password not applied)",
          token("patientfyu001@demo.local", "MyOwnPass99") and not token("patientfyu001@demo.local", "Different123")
          and token("nursefyu002@demo.local", PASSWORD) and not token("nursefyu002@demo.local", "Different123"))
    r = c.post(PREVIEW, json={**SPEC, "start": 1, "count": 50}, headers=H(at))
    check("preview after creation → 50 exists", r.get_json()["data"]["summary"]["exists"] == 50)

    # ================================================================ conflicts leave no partial pair
    c.post("/api/v1/admin/users", json={"email": "nursetst001@demo.local", "display_name": "既有護理師", "role": "nurse"}, headers=H(at))
    n_before = counts()
    r = c.post(CREATE, json={**SPEC, "patient_prefix": "patienttst", "nurse_prefix": "nursetst", "patient_name_prefix": "衝突測試病人",
                             "nurse_name_prefix": "衝突測試護理師", "start": 1, "count": 2,
                             "password": PASSWORD, "confirm": True}, headers=H(at))
    d = r.get_json()["data"]
    check("nurse account already exists → that pair conflict, the next one created", [x["status"] for x in d["rows"]] == ["conflict", "created"], d["rows"])
    check("conflict left nothing behind (no patienttst001 account, no 衝突測試病人 001)", counts() == (n_before[0] + 2, n_before[1] + 1, n_before[2] + 1)
          and db.session.query(User).filter_by(email="patienttst001@demo.local").first() is None
          and db.session.query(PatientProfile).filter_by(display_name="衝突測試病人 001").first() is None)
    existing_nurse = db.session.query(User).filter_by(email="nursetst001@demo.local").one()
    check("the pre-existing account was not changed (still must change its temporary password)", existing_nurse.password_changed_at is None
          and db.session.query(NursePatientAssignment).filter_by(nurse_id=existing_nurse.id).count() == 0)

    n_before = counts()
    r = c.post(CREATE, json={**SPEC, "patient_prefix": "patientdup", "nurse_prefix": "nursedup", "start": 2, "count": 1,
                             "password": PASSWORD, "confirm": True}, headers=H(at))
    check("a patient with the same name already exists (學生病人 002) → conflict, nothing created",
          r.get_json()["data"]["rows"][0]["status"] == "conflict" and counts() == n_before)

    # a failure mid-pair rolls the whole pair back
    real = m.create_assignment
    def broken(patient, admin, body):
        raise RuntimeError("simulated failure")
    m.create_assignment = broken
    n_before = counts()
    r = c.post(CREATE, json={**SPEC, "patient_prefix": "patientbrk", "nurse_prefix": "nursebrk", "patient_name_prefix": "復原測試病人",
                             "nurse_name_prefix": "復原測試護理師", "start": 1, "count": 2,
                             "password": PASSWORD, "confirm": True}, headers=H(at))
    m.create_assignment = real
    d = r.get_json()["data"]
    check("failure inside a pair → failed, whole pair rolled back (no nurse, patient or account left)",
          r.status_code == 201 and [x["status"] for x in d["rows"]] == ["failed", "failed"] and counts() == n_before
          and db.session.query(User).filter(User.email.like("%brk%")).count() == 0
          and db.session.query(PatientProfile).filter(PatientProfile.display_name.like("復原測試病人%")).count() == 0)
    r = c.post(CREATE, json={**SPEC, "patient_prefix": "patientbrk", "nurse_prefix": "nursebrk", "patient_name_prefix": "復原測試病人",
                             "nurse_name_prefix": "復原測試護理師", "start": 1, "count": 2,
                             "password": PASSWORD, "confirm": True}, headers=H(at))
    check("…and the same pairs can then be created cleanly", [x["status"] for x in r.get_json()["data"]["rows"]] == ["created", "created"])

    # ================================================================ normal account flows unchanged
    r = c.post("/api/v1/admin/users", json={"email": "normal.nurse@demo.local", "display_name": "一般護理師", "role": "nurse"}, headers=H(at))
    body = r.get_json()["data"]
    check("normal nurse creation unchanged: temporary password returned, must change on first login",
          r.status_code == 201 and body["temporary_password"] and body["must_change_password"] is True)
    r = c.post("/api/v1/patients", json={"display_name": "一般病人", "date_of_birth": "1960-01-01", "account": {"email": "normal.patient@demo.local"}}, headers=H(at))
    acc = r.get_json()["data"]["account"]
    tt = token("normal.patient@demo.local", acc["temporary_password"])
    check("normal patient account unchanged: forced password change on first login",
          acc["must_change_password"] is True and c.get("/api/v1/patients/me", headers=H(tt)).status_code == 403)

    # ================================================================ audit / existing data
    blob = json.dumps([[a.changes, a.reason] for a in db.session.query(AuditLog).all()], ensure_ascii=False)
    check("audit never holds the password (shared or own)", PASSWORD not in blob and "MyOwnPass99" not in blob and "Different123" not in blob)
    check("audit has the training records and batch summaries", db.session.query(AuditLog).filter_by(resource_type="training_accounts").count() >= 7
          and "training_account" in blob)
    after = snapshot()
    check("admin01, nurse01, patient01, patient02, P00001, P00002 and their assignments unchanged", before == after,
          [k for k in before[0] if before[0][k] != after[0].get(k)] + [k for k in before[1] if before[1][k] != after[1].get(k)])

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
