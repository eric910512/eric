import sys
from pathlib import Path
import uuid
import warnings
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import AuditLog, Notification, PatientCareAlert, PatientProfile, Role, User, VitalSign
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


def H(t, key="auto"):
    h = {"Authorization": f"Bearer {t}"}
    if key == "auto":
        key = str(uuid.uuid4())
    if key:
        h["Idempotency-Key"] = key
    return h


def post(t, key="auto", **fields):
    return c.post("/api/v1/vital-signs", json={"patient_id": "me", **fields}, headers=H(t, key))


def err(r):
    j = r.get_json() or {}
    return r.status_code, j.get("error", {}).get("code"), [d["field"] for d in j.get("error", {}).get("details", [])]


def codes(d):
    return [(a["alert_rule_code"], a["severity"], a["notified"]) for a in d["triggered_alerts"]]


with app.app_context():
    db.create_all()
    seed_dev_data()
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    other = PatientProfile(patient_code="P00002", display_name="測試病人 乙", date_of_birth=date(1960, 1, 1),
                           user=User(role=roles["patient"], email="p2@demo.local", password_hash=pw, display_name="乙", password_changed_at=utcnow()))
    db.session.add_all([other, User(role=roles["admin"], email="admin@demo.local", password_hash=pw, display_name="管理者", password_changed_at=utcnow())])
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    db.session.add(PatientCareAlert(patient_id=p1.id, alert_type="limb_restriction", body_site="right_arm",
                                    description="右手禁止注射及量血壓", severity="high"))
    db.session.commit()
    pt, nt, at = token("patient01@demo.local"), token("nurse01@demo.local"), token("admin@demo.local")
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()

    # ---------------- normal reading ----------------
    r = post(pt, temperature_c=36.85, temperature_site="ear", heart_rate_bpm=82, systolic_bp_mmhg=118,
             diastolic_bp_mmhg=76, bp_measure_site="left_arm", spo2_pct=98, weight_kg=64.25, respiratory_rate=16)
    d = r.get_json()["data"]
    normal_id = d["id"]
    check("201; cycle_day by server (Day 4); source patient_app; values rounded HALF_UP (36.85→36.9, 64.25→64.3)",
          r.status_code == 201 and d["cycle_day"] == 4 and d["source"] == "patient_app"
          and d["temperature_c"] == 36.9 and d["weight_kg"] == 64.3 and d["heart_rate_bpm"] == 82, {k: d[k] for k in ("temperature_c", "weight_kg")})
    check("normal → no flags, no alerts, no warnings", d["flags"] == [] and d["triggered_alerts"] == [] and d["warnings"] == [])
    audit = db.session.query(AuditLog).filter_by(action="CREATE", resource_type="vital_signs", resource_id=str(normal_id)).one()
    check("audit CREATE lists measured fields only (no values)",
          "temperature_c" in audit.changes["fields"] and "36" not in str(audit.changes) and audit.actor_role == "patient")

    # ---------------- fever outside nadir (Day 4) ----------------
    d = post(pt, temperature_c=38.4).get_json()["data"]
    check("fever outside nadir → 'fever' critical (not FN)", codes(d) == [("fever", "critical", True)], codes(d))
    check("flag message: chemo fever wording + critical", d["flags"][0]["level"] == "critical" and "化療期間發燒" in d["flags"][0]["message"])
    notes = db.session.query(Notification).filter_by(source_table="vital_signs", source_id=d["id"]).all()
    nurse_note = next(n for n in notes if n.recipient_id == nurse.id)
    check("notifications to patient + nurse; template renders value with unit",
          len(notes) == 2 and "體溫 38.4°C" in nurse_note.message and "Day 4" in nurse_note.message
          and "38.4°C" in next(n for n in notes if n.recipient_id == p1.user_id).message, nurse_note.message)
    d2 = post(pt, temperature_c=38.6).get_json()["data"]
    check("cooldown: second fever still advised but notified=false",
          codes(d2) == [("fever", "critical", False)] and "急診" in d2["triggered_alerts"][0]["message"])

    # ---------------- other rules ----------------
    d = post(pt, heart_rate_bpm=125, systolic_bp_mmhg=88, diastolic_bp_mmhg=58, bp_measure_site="left_arm", spo2_pct=89).get_json()["data"]
    check("HR 125 + SBP 88 + SpO2 89 → low_spo2 (critical first), tachycardia, hypotension",
          [x[0] for x in codes(d)] == ["low_spo2", "tachycardia", "hypotension"] or
          (codes(d)[0][0] == "low_spo2" and {x[0] for x in codes(d)} == {"low_spo2", "tachycardia", "hypotension"}), codes(d))
    check("flags carry direction text", {f["field"]: f["message"] for f in d["flags"]}.get("heart_rate_bpm", "").startswith("心跳偏快"),
          [f["message"] for f in d["flags"]])

    # ---------------- nadir: febrile neutropenia ----------------
    cycle = p1.chemotherapy_cycles[0]
    original_start = cycle.actual_start_date
    cycle.actual_start_date = original_start - timedelta(days=5)  # today becomes Day 9 (nadir 7–14)
    db.session.commit()
    d = post(pt, temperature_c=38.3).get_json()["data"]
    check("in nadir → 'suspected_febrile_neutropenia' (not plain fever); Day 9",
          codes(d) == [("suspected_febrile_neutropenia", "critical", True)] and d["cycle_day"] == 9, codes(d))
    check("flag message mentions nadir", "骨髓抑制期" in d["flags"][0]["message"])
    cycle.actual_start_date = original_start
    db.session.commit()

    # ---------------- limb restriction ----------------
    r = post(pt, systolic_bp_mmhg=120, diastolic_bp_mmhg=80, bp_measure_site="right_arm")
    d = r.get_json()["data"]
    check("BP on restricted right arm → accepted (201) with LIMB_RESTRICTION warning",
          r.status_code == 201 and [w["code"] for w in d["warnings"]] == ["LIMB_RESTRICTION"] and "右手" in d["warnings"][0]["message"])
    check("…and the warning is audited",
          db.session.query(AuditLog).filter_by(resource_type="vital_signs", resource_id=str(d["id"])).one().changes["warnings"] == ["LIMB_RESTRICTION"])

    # ---------------- validation ----------------
    for label, fields, expect in [
        ("no measurement", {"notes": "x"}, "values"),
        ("temperature 50", {"temperature_c": 50}, "temperature_c"),
        ("HR 88.5 (whole number)", {"heart_rate_bpm": 88.5}, "heart_rate_bpm"),
        ("systolic only", {"systolic_bp_mmhg": 120}, "diastolic_bp_mmhg"),
        ("systolic ≤ diastolic", {"systolic_bp_mmhg": 80, "diastolic_bp_mmhg": 90}, "systolic_bp_mmhg"),
        ("unknown site", {"temperature_c": 37, "temperature_site": "nose"}, "temperature_site"),
        ("site without value", {"heart_rate_bpm": 80, "bp_measure_site": "left_arm"}, "bp_measure_site"),
        ("boolean value", {"spo2_pct": True}, "spo2_pct"),
        ("string value", {"spo2_pct": "98"}, "spo2_pct"),
        ("future measured_at", {"spo2_pct": 98, "measured_at": (utcnow() + timedelta(hours=2)).isoformat() + "Z"}, "measured_at"),
        ("8 days ago", {"spo2_pct": 98, "measured_at": (utcnow() - timedelta(days=8)).isoformat() + "Z"}, "measured_at"),
    ]:
        status, code, fields_ = err(post(pt, **fields))
        check(f"400 {label}", status == 400 and code == "VALIDATION_ERROR" and expect in fields_, fields_)

    # ---------------- idempotency ----------------
    key = str(uuid.uuid4())
    n_before = db.session.query(Notification).count()
    r1 = post(pt, key, spo2_pct=88)
    n_mid = db.session.query(Notification).count()
    r2 = post(pt, key, spo2_pct=88)
    check("replay → same id, Idempotent-Replayed, same alerts, no new notifications",
          r1.get_json()["data"]["id"] == r2.get_json()["data"]["id"] and r2.headers.get("Idempotent-Replayed") == "true"
          and [a["alert_rule_code"] for a in r2.get_json()["data"]["triggered_alerts"]] == ["low_spo2"]
          and db.session.query(Notification).count() == n_mid)
    check("same key, different body → 422", err(post(pt, key, spo2_pct=95))[:2] == (422, "IDEMPOTENCY_KEY_MISMATCH"))
    check("patient without key → 428", err(post(pt, key=None, spo2_pct=97))[:2] == (428, "IDEMPOTENCY_KEY_REQUIRED"))
    r = c.post("/api/v1/vital-signs", json={"patient_id": p1.public_id, "temperature_c": 37.0}, headers=H(nt, key=None))
    check("nurse records for assigned patient without key → 201, source=nurse", r.status_code == 201 and r.get_json()["data"]["source"] == "nurse")

    # ---------------- authorization ----------------
    check("nurse → unassigned patient 404",
          c.post("/api/v1/vital-signs", json={"patient_id": other.public_id, "temperature_c": 37}, headers=H(nt)).status_code == 404)
    check("admin → 403", err(c.post("/api/v1/vital-signs", json={"patient_id": p1.public_id, "temperature_c": 37}, headers=H(at)))[:2] == (403, "FORBIDDEN"))
    check("no token → 401", c.post("/api/v1/vital-signs", json={"patient_id": "me", "temperature_c": 37}).status_code == 401)

    # ---------------- nurse abnormal list ----------------
    r = c.get("/api/v1/vital-signs/abnormal", headers=H(nt, key=None))
    body = r.get_json()
    ids = [i["id"] for i in body["data"]]
    check("abnormal list: only flagged readings, newest first; normal reading excluded",
          r.status_code == 200 and normal_id not in ids and ids == sorted(ids, reverse=True) and body["meta"]["critical"] >= 3, body["meta"])
    fever_item = next(i for i in body["data"] if any(a["alert_rule_code"] == "fever" for a in i["alerts"]))
    alert = next(a for a in fever_item["alerts"] if a["alert_rule_code"] == "fever")
    check("item carries patient, flags, severity and alert with my_notification_id",
          fever_item["patient"]["patient_code"] == "P00001" and fever_item["severity"] == "critical"
          and alert["resolved"] is False and alert["my_notification_id"])
    r = c.patch(f"/api/v1/notifications/{alert['my_notification_id']}/resolve",
                json={"resolution_note": "已電話聯繫，請病人至急診"}, headers=H(nt, key=None))
    check("resolve via the existing notification workflow → 200", r.status_code == 200, r.get_json().get("error"))
    again = next(i for i in c.get("/api/v1/vital-signs/abnormal", headers=H(nt, key=None)).get_json()["data"] if i["id"] == fever_item["id"])
    check("abnormal list shows the alert as resolved with note",
          next(a for a in again["alerts"] if a["alert_rule_code"] == "fever")["resolution_note"] == "已電話聯繫，請病人至急診")
    check("hours=200 → 400", c.get("/api/v1/vital-signs/abnormal?hours=200", headers=H(nt, key=None)).status_code == 400)
    check("patient → abnormal list 403", c.get("/api/v1/vital-signs/abnormal", headers=H(pt, key=None)).status_code == 403)
    rr = c.get("/api/v1/vital-signs/reference-ranges", headers=H(pt, key=None)).get_json()
    check("reference ranges available to signed-in users", rr["data"]["temperature_c"]["critical_high"] == 38.0 and rr["meta"]["source"] == "config_file")

    # ---------------- dashboards ----------------
    w = c.get("/api/v1/dashboard/patient/me", headers=H(pt, key=None)).get_json()["data"]["widgets"]
    check("patient dashboard: latest SpO2 88 flagged critical", w["latest-vitals"]["spo2_pct"]["value"] == 88
          and w["latest-vitals"]["spo2_pct"]["flag"] == "critical")
    nv = c.get(f"/api/v1/dashboard/patient/{p1.public_id}", headers=H(nt, key=None)).get_json()["data"]["widgets"]["nurse-view"]
    check("nurse-view: risk high with unresolved vital alerts", nv["risk"]["level"] == "high" and nv["unacknowledged_alerts"]["count"] >= 3,
          nv["risk"]["reasons"][:3])

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
