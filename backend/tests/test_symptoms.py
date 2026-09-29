import sys
from pathlib import Path
import uuid
import warnings
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import (AlertRule, AuditLog, IdempotencyRecord, Notification, PatientProfile, Role,
                        SymptomDefinition, SymptomRecord, User)
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


def post(tok, body, key="auto"):
    headers = {"Authorization": f"Bearer {tok}"}
    if key == "auto":
        key = str(uuid.uuid4())
    if key:
        headers["Idempotency-Key"] = key
    return c.post("/api/v1/symptoms/records", json=body, headers=headers)


def report(pain=2, nausea=3, fatigue=4, fever=False, **extra):
    return {"patient_id": "me", "form_code": "daily_chemo_check", "values": [
        {"definition_code": "pain", "value_numeric": pain},
        {"definition_code": "nausea", "value_numeric": nausea},
        {"definition_code": "fatigue", "value_numeric": fatigue},
        {"definition_code": "fever", "value_boolean": fever},
    ], **extra}


def err(resp):
    j = resp.get_json() or {}
    return resp.status_code, j.get("error", {}).get("code"), [d["field"] for d in j.get("error", {}).get("details", [])]


with app.app_context():
    db.create_all()
    seed_dev_data()
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    other = PatientProfile(patient_code="P00002", display_name="測試病人 乙", date_of_birth=date(1960, 1, 1),
                           user=User(role=roles["patient"], email="p2@demo.local", password_hash=pw, display_name="乙", password_changed_at=utcnow()))
    db.session.add_all([other, User(role=roles["admin"], email="admin@demo.local", password_hash=pw, display_name="管理者", password_changed_at=utcnow())])
    db.session.commit()
    pt, nt, at = token("patient01@demo.local"), token("nurse01@demo.local"), token("admin@demo.local")
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()

    # ---------------- form ----------------
    r = c.get("/api/v1/symptoms/forms/daily_chemo_check", headers={"Authorization": f"Bearer {pt}"})
    f = r.get_json()["data"]
    codes = [i["definition"]["code"] for i in f["items"]]
    check("GET form 200 with 4 ordered items", r.status_code == 200 and codes == ["pain", "nausea", "fatigue", "fever"], codes)
    check("scale item carries min/max/step/labels; boolean has none",
          f["items"][0]["definition"]["max_value"] == 10 and f["items"][0]["definition"]["min_label"] == "沒有"
          and "min_value" not in f["items"][3]["definition"] and f["items"][3]["definition"]["value_type"] == "boolean")
    check("unknown form → 404", c.get("/api/v1/symptoms/forms/nope", headers={"Authorization": f"Bearer {pt}"}).status_code == 404)
    check("form without token → 401", c.get("/api/v1/symptoms/forms/daily_chemo_check").status_code == 401)

    # ---------------- happy path ----------------
    r = post(pt, report(pain=2, nausea=3, fatigue=4))
    d = r.get_json()["data"]
    check("POST 201; source=patient_app; cycle_day computed by server (Day 4)",
          r.status_code == 201 and d["source"] == "patient_app" and d["cycle_day"] == 4 and d["review_status"] == "submitted",
          {k: d[k] for k in ("id", "cycle_day", "source")})
    check("values echoed with scores; boolean score 0",
          [(v["definition_code"], v["score"]) for v in d["values"]] == [("pain", 2), ("nausea", 3), ("fatigue", 4), ("fever", 0)])
    check("no thresholds met → no alerts, no notifications",
          d["triggered_alerts"] == [] and db.session.query(Notification).filter_by(source_table="symptom_records").count() == 0)
    audit = db.session.query(AuditLog).filter_by(action="CREATE", resource_type="symptom_records", resource_id=str(d["id"])).one()
    check("audit CREATE with actor, patient and definition codes (no scores)",
          audit.actor_user_id and audit.patient_id == p1.id and audit.changes["definition_codes"] == ["pain", "nausea", "fatigue", "fever"]
          and "value" not in str(audit.changes))

    # ---------------- alerts ----------------
    r = post(pt, report(pain=8, nausea=2, fatigue=3))
    d = r.get_json()["data"]
    notes = db.session.query(Notification).filter_by(source_table="symptom_records", source_id=d["id"]).all()
    check("pain 8 → severe_pain warning in response",
          [(a["alert_rule_code"], a["severity"], a["notified"]) for a in d["triggered_alerts"]] == [("severe_pain", "warning", True)],
          d["triggered_alerts"][0]["message"])
    check("notifications: 1 patient + 1 nurse, shared event_key, risk_alert",
          sorted(n.recipient_id for n in notes) == sorted([p1.user_id, nurse.id])
          and len({n.event_key for n in notes}) == 1 and all(n.type == "risk_alert" for n in notes))
    nurse_note = next(n for n in notes if n.recipient_id == nurse.id)
    check("nurse message rendered from template", "P00001" in nurse_note.message and "疼痛 8/10" in nurse_note.message
          and "Day 4" in nurse_note.message, nurse_note.message)

    r = post(pt, report(pain=9))
    d = r.get_json()["data"]
    check("cooldown: second pain alert still shown to patient but notified=false, no new notifications",
          d["triggered_alerts"][0]["notified"] is False
          and db.session.query(Notification).filter_by(source_id=d["id"], source_table="symptom_records").count() == 0)

    r = post(pt, report(fever=True))
    d = r.get_json()["data"]
    check("fever → critical alert first, patient message tells to call/ER",
          d["triggered_alerts"][0]["alert_rule_code"] == "reported_fever" and d["triggered_alerts"][0]["severity"] == "critical"
          and "急診" in d["triggered_alerts"][0]["message"])

    # extra_conditions: consecutive_records + within_nadir
    fatigue_def = db.session.query(SymptomDefinition).filter_by(code="fatigue").one()
    nausea_def = db.session.query(SymptomDefinition).filter_by(code="nausea").one()
    rule = AlertRule(code="fatigue_twice", name="連續疲倦", source_type="symptom", symptom_definition=fatigue_def,
                     operator=">=", threshold_value=Decimal("6"), severity="info", extra_conditions={"consecutive_records": 2})
    nadir_rule = AlertRule(code="nausea_nadir", name="抑制期噁心", source_type="symptom",
                           symptom_definition=nausea_def,
                           operator=">=", threshold_value=Decimal("1"), severity="info", extra_conditions={"within_nadir": True})
    db.session.add_all([rule, nadir_rule])
    db.session.commit()
    first = post(pt, report(fatigue=6)).get_json()["data"]["triggered_alerts"]
    second = post(pt, report(fatigue=7)).get_json()["data"]["triggered_alerts"]
    check("consecutive_records=2: first report no, second report yes",
          "fatigue_twice" not in [a["alert_rule_code"] for a in first] and "fatigue_twice" in [a["alert_rule_code"] for a in second])
    check("within_nadir: Day 4 is before nadir (Day 7–14) → not triggered",
          "nausea_nadir" not in [a["alert_rule_code"] for a in second])
    rule.is_active = nadir_rule.is_active = False
    db.session.commit()

    # ---------------- idempotency ----------------
    key = str(uuid.uuid4())
    body = report(pain=1, nausea=1, fatigue=1)
    before = db.session.query(SymptomRecord).count()
    r1, r2 = post(pt, body, key), post(pt, body, key)
    check("same key + same body → replay: same record, 201, Idempotent-Replayed header, no duplicate row",
          r1.status_code == r2.status_code == 201 and r1.get_json()["data"]["id"] == r2.get_json()["data"]["id"]
          and r2.headers.get("Idempotent-Replayed") == "true" and db.session.query(SymptomRecord).count() == before + 1)
    r3 = post(pt, report(pain=5), key)
    check("same key + different body → 422 IDEMPOTENCY_KEY_MISMATCH", err(r3)[:2] == (422, "IDEMPOTENCY_KEY_MISMATCH"))
    alert_key = str(uuid.uuid4())
    a1 = post(pt, report(pain=1, fever=True), alert_key)
    n_before = db.session.query(Notification).count()
    a2 = post(pt, report(pain=1, fever=True), alert_key)
    check("replay of an alerting report returns the alerts but creates no new notifications",
          [x["alert_rule_code"] for x in a2.get_json()["data"]["triggered_alerts"]] == ["reported_fever"]
          and db.session.query(Notification).count() == n_before)
    check("patient without Idempotency-Key → 428", err(post(pt, report(), key=None))[:2] == (428, "IDEMPOTENCY_KEY_REQUIRED"))
    rec = db.session.query(IdempotencyRecord).filter_by(idempotency_key=key).one()
    check("idempotency row stores pointer only", rec.resource_type == "symptom_records" and rec.status == "completed")

    # ---------------- validation ----------------
    cases = [
        ("out of range 11", report(pain=11), "values[0].value_numeric"),
        ("step 0.5 on integer scale", report(pain=2.5), "values[0].value_numeric"),
        ("bool sent as number", {**report(), "values": report()["values"][:3] + [{"definition_code": "fever", "value_boolean": 1}]}, "values[3].value_boolean"),
        ("string number", {**report(), "values": [{"definition_code": "pain", "value_numeric": "5"}] + report()["values"][1:]}, "values[0].value_numeric"),
        ("missing required fever", {**report(), "values": report()["values"][:3]}, "values"),
        ("unknown definition", {**report(), "values": report()["values"] + [{"definition_code": "headache", "value_numeric": 1}]}, "values[4].definition_code"),
        ("duplicate definition", {**report(), "values": report()["values"] + [{"definition_code": "pain", "value_numeric": 1}]}, "values[4].definition_code"),
        ("wrong key for type", {**report(), "values": [{"definition_code": "pain", "value_text": "x"}] + report()["values"][1:]}, "values[0]"),
        ("future recorded_at", report(recorded_at=(utcnow() + timedelta(hours=1)).isoformat() + "Z"), "recorded_at"),
        ("naive recorded_at", report(recorded_at="2026-09-24T01:00:00"), "recorded_at"),
        ("unknown form", {**report(), "form_code": "nope"}, "form_code"),
        ("empty values", {**report(), "values": []}, "values"),
    ]
    for label, body, field in cases:
        status, code, fields = err(post(pt, body))
        check(f"400 {label}", status == 400 and code == "VALIDATION_ERROR" and field in fields, fields)
    check("missing patient_id → 400", err(post(pt, {"form_code": "daily_chemo_check", "values": []}))[0] == 400)

    # ---------------- authorization ----------------
    r = post(nt, {**report(pain=3), "patient_id": p1.public_id}, key=None)
    check("nurse submits for assigned patient without key → 201, source=nurse",
          r.status_code == 201 and r.get_json()["data"]["source"] == "nurse")
    check("nurse → unassigned patient 404", post(nt, {**report(), "patient_id": other.public_id}).status_code == 404)
    check("patient → other patient 404", post(pt, {**report(), "patient_id": other.public_id}).status_code == 404)
    check("admin cannot write clinical data → 403", err(post(at, {**report(), "patient_id": p1.public_id}))[:2] == (403, "FORBIDDEN"))
    check("no token → 401", c.post("/api/v1/symptoms/records", json=report()).status_code == 401)

    # ---------------- dashboard reflects the report ----------------
    dash = c.get("/api/v1/dashboard/patient/me", headers={"Authorization": f"Bearer {pt}"}).get_json()["data"]["widgets"]
    check("dashboard: reported_today=true", dash["symptom-quick-report"]["reported_today"] is True)
    check("dashboard: trend has today's max pain (9)",
          next(s for s in dash["symptom-trend"]["series"] if s["key"] == "pain")["points"][-1]["value"] == 9.0)
    check("dashboard: boolean fever not drawn on the 0–10 trend", "fever" not in [s["key"] for s in dash["symptom-trend"]["series"]])
    check("dashboard: patient sees risk notifications", dash["notifications"]["unread_count"] >= 3, dash["notifications"]["unread_count"])
    nv = c.get(f"/api/v1/dashboard/patient/{p1.public_id}", headers={"Authorization": f"Bearer {nt}"}).get_json()["data"]["widgets"]["nurse-view"]
    check("nurse-view: risk high (critical fever alert unacknowledged), alerts deduped per event",
          nv["risk"]["level"] == "high" and nv["unacknowledged_alerts"]["count"] >= 2, nv["risk"]["reasons"][:3])

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
