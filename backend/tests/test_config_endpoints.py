"""Sprint 0: GET /api/v1/settings/public and GET /api/v1/dashboard/layout."""
import sys
from pathlib import Path
import warnings
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import AuditLog, PatientProfile, Role, User
from app.modules.dashboard.layouts import PATIENT_HOME_LAYOUT
from app.models.base import utcnow
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()


def token(email):
    return c.post("/api/v1/auth/login", json={"email": email, "password": "Demo@1234"}).get_json()["data"]["access_token"]


def H(t):
    return {"Authorization": f"Bearer {t}"}


with app.app_context():
    db.create_all()
    seed_dev_data()
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    u2 = User(role=roles["patient"], email="p2@demo.local", password_hash=pw, display_name="乙", password_changed_at=utcnow())
    p2 = PatientProfile(patient_code="P00002", display_name="測試病人 乙", date_of_birth=date(1960, 1, 1), user=u2)
    db.session.add_all([p2, User(role=roles["admin"], email="admin@demo.local", password_hash=pw, display_name="管理者", password_changed_at=utcnow())])
    db.session.commit()
    pt, p2t, nt, at = (token(e) for e in ("patient01@demo.local", "p2@demo.local", "nurse01@demo.local", "admin@demo.local"))
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()

    # ---------------- settings/public ----------------
    r = c.get("/api/v1/settings/public")
    b = r.get_json()
    check("settings/public without a token → 200 (the login page needs it)", r.status_code == 200)
    check("contract: data = organization / contacts / disclaimers, meta.source = config_file",
          set(b["data"]) == {"organization", "contacts", "disclaimers"} and b["meta"] == {"source": "config_file"})
    check("content comes from Config.INSTITUTION", b["data"] == app.config["INSTITUTION"])
    check("same response for signed-in users", c.get("/api/v1/settings/public", headers=H(pt)).get_json() == b)
    b["data"]["organization"]["name"] = "changed"
    check("response is a copy (config not mutated)", app.config["INSTITUTION"]["organization"]["name"] == "Demo 醫院")
    check("POST settings/public → 405", c.post("/api/v1/settings/public").status_code == 405)
    check("no audit row for public settings", db.session.query(AuditLog).filter_by(resource_type="settings").count() == 0)

    # ---------------- dashboard/layout ----------------
    check("layout without a token → 401", c.get("/api/v1/dashboard/layout").status_code == 401)
    r = c.get("/api/v1/dashboard/layout", headers=H(pt))
    lay = r.get_json()
    check("patient → 200 with the §8.1 contract", r.status_code == 200 and lay["data"]["schema_version"] == 1
          and lay["data"]["source"] == "code_default" and lay["meta"] == {"role": "patient", "context": "overview"})
    check("layout = the code default", lay["data"] == PATIENT_HOME_LAYOUT)
    check("pinned patient-summary; items in the patient home order",
          [i["widget_code"] for i in lay["data"]["pinned"]] == ["patient-summary"]
          and [i["widget_code"] for i in lay["data"]["items"]] == ["risk-summary", "today-schedule", "treatment-progress",
                                                                    "symptom-quick-report", "latest-vitals", "lab-summary",
                                                                    "symptom-trend", "my-notifications"]
          and [i["position"]["y"] for i in lay["data"]["items"]] == list(range(8)))
    check("Phase 1 permissions: no editing", lay["data"]["permissions"] == {"can_edit": False, "can_reorder": False,
                                                                            "can_remove": False, "can_collapse": True})
    d = c.get("/api/v1/dashboard/patient/me", headers=H(pt)).get_json()["data"]["widgets"]
    known = set(d) | {"my-notifications"}  # the dashboard payload key for my-notifications is "notifications"
    check("every layout widget has data in the patient dashboard payload",
          all(i["widget_code"] in known for i in lay["data"]["pinned"] + lay["data"]["items"]))
    check("context=patient with the own id / me → 200",
          c.get(f"/api/v1/dashboard/layout?context=patient&patient_id={p1.public_id}", headers=H(pt)).status_code == 200
          and c.get("/api/v1/dashboard/layout?context=patient&patient_id=me", headers=H(pt)).status_code == 200)
    check("context=patient with another patient's id → 404",
          c.get(f"/api/v1/dashboard/layout?context=patient&patient_id={p1.public_id}", headers=H(p2t)).status_code == 404)
    check("bad context → 400", c.get("/api/v1/dashboard/layout?context=admin", headers=H(pt)).status_code == 400)
    for name, t in (("nurse", nt), ("admin", at)):
        r = c.get("/api/v1/dashboard/layout", headers=H(t))
        check(f"{name} → 404 NOT_FOUND (fixed screens in Phase 1)", r.status_code == 404 and r.get_json()["error"]["code"] == "NOT_FOUND")
    lay["data"]["items"].clear()
    check("response is a copy (code default not mutated)", len(PATIENT_HOME_LAYOUT["items"]) == 8)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
