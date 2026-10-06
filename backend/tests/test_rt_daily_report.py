"""每日症狀與自我照護回報 (rt_daily_report): the form exactly as given (sections, question texts,
options and their order), submission through the existing symptom API, one report per patient per
local day for this form only, option labels for nurses, "needs attention" = the existing score ≥ 7
rule (no alert rule applies), the existing review workflow, permissions, and the data migration
(identical to the seed; downgrade keeps the form once reports exist)."""
import os
import sys
from pathlib import Path
import json
import tempfile
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
from app.models import (AlertRule, AuditLog, Notification, NursePatientAssignment, PatientProfile, Role, SymptomDefinition,
                        SymptomForm, SymptomRecord, User)
from app.models.base import utcnow
from app.seeds import rt_daily_report as seed_data
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()
URL = "/api/v1/symptoms/records"
CODE = "rt_daily_report"
EXPECTED = [  # (code, section, question, options in order)
    ("rt_pain_skin", "過去 24 小時症狀自主管理", "疼痛－放射線皮膚發紅、脫皮導致", None),
    ("rt_pain_oral", "過去 24 小時症狀自主管理", "疼痛－口腔黏膜紅腫、發炎導致", None),
    ("rt_dermatitis_redness", "過去 24 小時症狀自主管理", "放射線皮膚炎－發紅情形", ["無", "淺紅、粉紅", "深紅"]),
    ("rt_dermatitis_desquamation", "過去 24 小時症狀自主管理", "放射線皮膚炎－脫皮、脫屑情形",
     ["無", "乾燥、有脫皮脫屑", "潮濕、有脫皮脫屑", "有脫皮脫屑伴出血"]),
    ("rt_appetite_poor", "過去 24 小時症狀自主管理", "食慾不佳", None),
    ("rt_fatigue", "過去 24 小時症狀自主管理", "疲倦", None),
    ("self_care_moisturizer", "我的每日自評", "我今天擦保濕乳液或醫師開的藥膏了嗎？", ["有", "沒有"]),
    ("self_care_towel_pat", "我的每日自評", "我今天洗完澡有用毛巾「按壓」，沒有來回摩擦皮膚嗎？", ["有", "沒有"]),
    ("self_care_mouth_rinse", "我的每日自評", "除了睡覺以外，我有每個小時，以及飯後都有確實漱口嗎？", ["有", "沒有"]),
]


def token(email, password="Demo@1234"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password}).get_json()["data"]["access_token"]


def H(t, key=None):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": key or str(uuid.uuid4())}


def answers(**over):
    base = {"rt_pain_skin": 3, "rt_pain_oral": 8, "rt_dermatitis_redness": "dark", "rt_dermatitis_desquamation": "moist",
            "rt_appetite_poor": 7, "rt_fatigue": 4, "self_care_moisturizer": "yes", "self_care_towel_pat": "no",
            "self_care_mouth_rinse": "yes"}
    base.update(over)
    return [{"definition_code": k, ("value_numeric" if isinstance(v, int) else "option_code"): v} for k, v in base.items() if v is not None]


def submit(t, pid="me", key=None, **over):
    r = c.post(URL, json={"patient_id": pid, "form_code": CODE, "values": answers(**over)}, headers=H(t, key))
    return r, (r.get_json() or {})


with app.app_context():
    db.create_all()
    seed_dev_data()
    roles = {r.name: r for r in db.session.query(Role)}
    pw = generate_password_hash("Demo@1234")
    nurse2 = User(role=roles["nurse"], email="nurse02@demo.local", password_hash=pw, display_name="測試護理師 陳", password_changed_at=utcnow())
    u2 = User(role=roles["patient"], email="p2@demo.local", password_hash=pw, display_name="乙", password_changed_at=utcnow())
    p2 = PatientProfile(patient_code="P09002", display_name="測試病人 乙", date_of_birth=date(1960, 1, 1), user=u2)
    db.session.add_all([nurse2, p2])
    db.session.flush()
    db.session.add(NursePatientAssignment(nurse_id=nurse2.id, patient_id=p2.id))
    db.session.commit()
    pt, p2t, nt, nt2 = (token(e) for e in ("patient01@demo.local", "p2@demo.local", "nurse01@demo.local", "nurse02@demo.local"))
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    pid = p1.public_id

    # ================================================================ the form, exactly as given
    r = c.get(f"/api/v1/symptoms/forms/{CODE}", headers=H(pt))
    form = r.get_json()["data"]
    check("GET form → 200, name 每日症狀與自我照護回報", r.status_code == 200 and form["name"] == "每日症狀與自我照護回報", form.get("name"))
    got = [(i["definition"]["code"], i["definition"]["category"]["name_zh"], i["definition"]["question_text"],
            [o["label_zh"] for o in i["definition"]["options"]] if "options" in i["definition"] else None) for i in form["items"]]
    check("questions, sections, texts and option order exactly as given", got == EXPECTED, got)
    check("all 9 questions required", all(i["is_required"] for i in form["items"]) and len(form["items"]) == 9)
    scales = [i["definition"] for i in form["items"] if i["definition"]["value_type"] == "scale"]
    check("0–10 questions: min 0, max 10, step 1, no extra end labels", all(d["min_value"] == 0 and d["max_value"] == 10 and d["step"] == 1
          and d["min_label"] is None and d["max_label"] is None for d in scales) and len(scales) == 4)
    check("self-care questions are 有 / 沒有 (in that order)", [o["value_code"] for o in form["items"][6]["definition"]["options"]] == ["yes", "no"])
    check("existing form daily_chemo_check unchanged (4 questions, now with a category too)",
          [i["definition"]["code"] for i in c.get("/api/v1/symptoms/forms/daily_chemo_check", headers=H(pt)).get_json()["data"]["items"]]
          == ["pain", "nausea", "fatigue", "fever"])
    check("no alert rule points at the new questions", db.session.query(AlertRule).join(SymptomDefinition)
          .filter(SymptomDefinition.code.in_([e[0] for e in EXPECTED])).count() == 0)
    check("疲倦 is its own definition, not the existing fatigue one", form["items"][5]["definition"]["code"] == "rt_fatigue")

    # ================================================================ submit
    before_alerts = db.session.query(Notification).count()
    key = str(uuid.uuid4())
    r, body = submit(pt, key=key)
    d = body.get("data", {})
    check("patient submits → 201", r.status_code == 201, body)
    check("stored as a symptom record of rt_daily_report (patient_app, submitted)", d["form"]["code"] == CODE and d["source"] == "patient_app"
          and d["review_status"] == "submitted" and len(d["values"]) == 9)
    vals = {v["definition_code"]: v for v in d["values"]}
    check("single-choice answers carry the option label", vals["rt_dermatitis_redness"]["option_label"] == "深紅"
          and vals["self_care_towel_pat"]["option_label"] == "沒有" and vals["self_care_moisturizer"]["option_code"] == "yes")
    check("0–10 answers keep their score", vals["rt_pain_oral"]["value_numeric"] == 8 and vals["rt_pain_oral"]["score"] == 8)
    check("no alert raised (no alert rule for this form)", d["triggered_alerts"] == [] and db.session.query(Notification).count() == before_alerts)
    rec_id = d["id"]
    r2, b2 = submit(pt, key=key)
    check("same Idempotency-Key → same record (replay, not a duplicate)", r2.status_code == 201 and b2["data"]["id"] == rec_id)

    # ================================================================ once per day (this form only)
    r, body = submit(pt, rt_pain_skin=1)
    check("second report the same day → 409 ALREADY_REPORTED_TODAY", r.status_code == 409 and body["error"]["code"] == "ALREADY_REPORTED_TODAY"
          and body["error"]["message"] == "今天已回報" and body["error"]["details"][0]["record_id"] == rec_id, body)
    check("…nothing stored", db.session.query(SymptomRecord).join(SymptomForm).filter(SymptomForm.code == CODE).count() == 1)
    r, body = submit(nt, pid=pid, rt_pain_skin=1)
    check("a nurse entering it for the same patient the same day → 409 too (one per patient per day)", r.status_code == 409)
    r = c.post(URL, json={"patient_id": "me", "form_code": "daily_chemo_check", "values": [
        {"definition_code": "pain", "value_numeric": 2}, {"definition_code": "nausea", "value_numeric": 1},
        {"definition_code": "fatigue", "value_numeric": 1}, {"definition_code": "fever", "value_boolean": False}]}, headers=H(pt))
    r_again = c.post(URL, json={"patient_id": "me", "form_code": "daily_chemo_check", "values": [
        {"definition_code": "pain", "value_numeric": 3}, {"definition_code": "nausea", "value_numeric": 1},
        {"definition_code": "fatigue", "value_numeric": 1}, {"definition_code": "fever", "value_boolean": False}]}, headers=H(pt))
    check("other forms unaffected: daily_chemo_check twice the same day → 201, 201", r.status_code == 201 and r_again.status_code == 201)
    r, body = submit(p2t)
    check("another patient the same day → 201 (per patient)", r.status_code == 201)
    rec = db.session.get(SymptomRecord, rec_id)
    rec.recorded_at -= timedelta(days=1)  # yesterday's report
    db.session.commit()
    r, body = submit(pt)
    check("next local day → 201 again", r.status_code == 201, body.get("error"))
    today_id = body["data"]["id"]

    # entered in error → the day is free again; a correction does not count as a second report
    r = c.post(f"{URL}/{today_id}/mark-error", json={"reason": "填錯病人"}, headers=H(nt))
    r2, body = submit(pt)
    check("after mark-error the patient can report again that day", r.status_code == 200 and r2.status_code == 201, (r.status_code, r2.status_code))
    today_id = body["data"]["id"]
    r = c.post(f"{URL}/{today_id}/amend", json={"amend_reason": "病人來電更正", "values": answers(rt_fatigue=6)}, headers=H(nt))
    check("nurse correction (amend) of today's report is allowed", r.status_code == 201, r.get_json())

    # ================================================================ validation
    r, body = submit(p2t, self_care_towel_pat=None)
    check("missing answer → 400 (all questions required) — checked before the once-per-day rule", r.status_code == 400)
    r = c.post(URL, json={"patient_id": "me", "form_code": CODE, "values": answers(rt_dermatitis_redness="purple")}, headers=H(p2t))
    check("unknown option → 400", r.status_code == 400 and "option_code" in json.dumps(r.get_json()))
    r = c.post(URL, json={"patient_id": "me", "form_code": CODE, "values": answers(rt_pain_oral=11)}, headers=H(p2t))
    check("0–10 out of range → 400", r.status_code == 400)

    # ================================================================ nurse view
    lst = c.get(f"{URL}/{pid}?form_code={CODE}", headers=H(nt)).get_json()
    check("nurse lists the patient's daily reports (form_code filter)", lst["meta"]["total"] >= 2
          and all(x["form"]["code"] == CODE for x in lst["data"]), lst["meta"])
    first = lst["data"][0]
    check("each report: date/time, reporter, every answer with its label", first["recorded_at"] and first["reported_by"]
          and len(first["values"]) == 9 and all(("option_label" in v) or ("value_numeric" in v) for v in first["values"]))
    all_forms = c.get(f"{URL}/{pid}", headers=H(nt)).get_json()
    check("without the filter the list still has every form", any(x["form"]["code"] == "daily_chemo_check" for x in all_forms["data"])
          and any(x["form"]["code"] == CODE for x in all_forms["data"]))
    check("nurse not assigned → 404", c.get(f"{URL}/{pid}?form_code={CODE}", headers=H(nt2)).status_code == 404)
    check("patient cannot list another patient's reports → 404", c.get(f"{URL}/{p2.public_id}?form_code={CODE}", headers=H(pt)).status_code == 404)
    pending = c.get("/api/v1/dashboard/widgets/pending-symptom-reviews/data", headers=H(nt)).get_json()["data"]
    mine = [x for x in pending if x["id"] in {i["id"] for i in lst["data"]}]
    check("pending-review summary shows option labels for single-choice answers", mine and "放射線皮膚炎－發紅情形：深紅" in mine[0]["summary"]
          and "放射線皮膚炎－發紅情形 2" not in mine[0]["summary"], mine[0]["summary"] if mine else pending)

    # needs attention: the existing score >= 7 rule
    widgets = c.get(f"/api/v1/dashboard/patient/{pid}", headers=H(nt)).get_json()["data"]["widgets"]
    reasons = widgets["nurse-view"]["risk"]["reasons"]
    check("existing ≥ 7 rule reads the 0–10 answers (口腔黏膜疼痛 8 / 食慾不佳 7)", any("疼痛－口腔黏膜紅腫、發炎導致 8" in x for x in reasons)
          and any("食慾不佳 7" in x for x in reasons), reasons)
    check("…and nothing from single-choice or self-care answers", not any("發紅情形" in x or "毛巾" in x for x in reasons))

    # review (existing workflow)
    target = lst["data"][0]["id"]
    r = c.post(f"{URL}/{target}/review", json={"action_note": "已電話衛教皮膚照護", "assessment_type": "phone_follow_up"}, headers=H(nt))
    check("nurse reviews a daily report with the existing review flow", r.status_code == 200, r.get_json())
    rec = db.session.get(SymptomRecord, target)
    subjective = rec.nursing_assessment.subjective if rec.nursing_assessment else ""
    check("review summary uses option labels (not option scores)", "發紅情形：深紅" in subjective and "毛巾「按壓」，沒有來回摩擦皮膚嗎？：沒有" in subjective, subjective)
    check("patient sees own daily reports, no reviewer names", (lambda b: b["data"] and all(x["reviewed_by"] is None for x in b["data"]))(
        c.get(f"{URL}/me?form_code={CODE}", headers=H(pt)).get_json()))
    audit = db.session.query(AuditLog).filter_by(resource_type="symptom_records", action="CREATE").count()
    check("submissions audited (existing CREATE symptom_records)", audit >= 3)

# ================================================================ data migration = seed; protected downgrade
with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
    from app.config import DevelopmentConfig

    # a separate app on a throw-away SQLite file, migrated with Alembic (the engine is built at app creation)
    saved = (DevelopmentConfig.SQLALCHEMY_DATABASE_URI, DevelopmentConfig.EMAIL_CAPTURE_DIR)
    DevelopmentConfig.SQLALCHEMY_DATABASE_URI = f"sqlite:///{Path(tmp, 'mig.db').as_posix()}"
    DevelopmentConfig.EMAIL_CAPTURE_DIR = None
    mig = create_app("development")
    DevelopmentConfig.SQLALCHEMY_DATABASE_URI, DevelopmentConfig.EMAIL_CAPTURE_DIR = saved
    from flask_migrate import downgrade, upgrade
    from sqlalchemy import text

    migrations = str(Path(__file__).resolve().parents[1] / "migrations")

    def snapshot():
        rows = db.session.execute(text(
            "SELECT d.code, c.code, c.name_zh, d.name_zh, d.question_text, d.value_type, d.min_value, d.max_value, d.step,"
            " d.higher_is_worse, i.display_order, i.is_required FROM symptom_form_items i JOIN symptom_forms f ON f.id = i.form_id"
            " JOIN symptom_definitions d ON d.id = i.definition_id JOIN symptom_categories c ON c.id = d.category_id"
            " WHERE f.code = 'rt_daily_report' ORDER BY i.display_order")).all()
        options = db.session.execute(text(
            "SELECT d.code, o.value_code, o.label_zh, o.score, o.display_order FROM symptom_definition_options o"
            " JOIN symptom_definitions d ON d.id = o.definition_id WHERE d.code LIKE 'rt_%' OR d.code LIKE 'self_care_%'"
            " ORDER BY d.code, o.display_order")).all()
        form = db.session.execute(text("SELECT name, intended_for, recall_period_hours, availability, version, is_active"
                                        " FROM symptom_forms WHERE code = 'rt_daily_report'")).first()
        norm = lambda r: tuple(float(x) if hasattr(x, "as_tuple") else (bool(x) if isinstance(x, bool) else x) for x in r)
        return [norm(r) for r in rows], [norm(r) for r in options], tuple(form) if form else None

    with mig.app_context(), warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # migrations/env.py uses Flask-SQLAlchemy's get_engine()
        upgrade(directory=migrations)
        migrated = snapshot()
        seed_dev_data()
        db.session.commit()
        seeded = snapshot()
        check("data migration creates exactly what the seed defines (form, questions, options)", migrated == seeded and len(migrated[0]) == 9
              and len(migrated[1]) == 13, (migrated[0][:1], seeded[0][:1]))
        # a report exists → downgrade keeps the form
        user_id = db.session.execute(text("SELECT id FROM users WHERE email = 'patient01@demo.local'")).scalar()
        patient_id = db.session.execute(text("SELECT id FROM patient_profiles WHERE patient_code = 'P00001'")).scalar()
        form_id = db.session.execute(text("SELECT id FROM symptom_forms WHERE code = 'rt_daily_report'")).scalar()
        now = utcnow()
        db.session.execute(text("INSERT INTO symptom_records (patient_id, form_id, form_version, recorded_at, reported_by, review_status,"
                                " source, record_status, created_at, updated_at) VALUES (:p, :f, 1, :n, :u, 'submitted', 'patient_app',"
                                " 'final', :n, :n)"), {"p": patient_id, "f": form_id, "n": now, "u": user_id})
        db.session.commit()
        downgrade(directory=migrations, revision="470df9cf2163")
        kept = db.session.execute(text("SELECT COUNT(*) FROM symptom_forms WHERE code = 'rt_daily_report'")).scalar()
        check("downgrade with a report present keeps the form and its questions", kept == 1)
        db.session.remove()
        db.engine.dispose()  # Windows: close the SQLite file before the temp directory is removed

# literal-data parity without importing the migration as a package
mig_source = (Path(__file__).resolve().parents[1] / "migrations" / "versions" / "f1a7c3d2b9e4_rt_daily_report_form.py").read_text(encoding="utf-8")
ns = {}
exec(compile(mig_source.split("def _now():")[0].replace("from alembic import op", "").replace("import sqlalchemy as sa", ""), "migration", "exec"), ns)
check("migration literal data == seed module data", ns["DEFINITIONS"] == seed_data.DEFINITIONS and ns["CATEGORIES"] == seed_data.CATEGORIES
      and ns["FORM_NAME"] == seed_data.FORM["name"])

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
