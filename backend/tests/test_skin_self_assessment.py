"""放射治療皮膚反應自我評估 (rt_skin_self_assessment, Phase 1a, no photos): the form (reusing three
rt_daily_report questions as they are, everything optional, no grade wording for patients), submission
through the existing symptom API (notes, the 「不確定，請護理師協助評估」 request, idempotency, not once per
day, no alert rule), no effect on the daily report / 今日已回報 / self-care stamps / the home chart, the
patient's own history (no professional grade, no nurse note), the nurse queue and the existing review flow
with an optional follow-up (需追蹤) that never overwrites the patient's answers, access control, audit, and
the data migration (identical to the seed; never touches rt_daily_report; protected downgrade) — on
throw-away databases only."""
import os
import sys
from pathlib import Path
import json
import tempfile
import uuid
import warnings
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import (AlertRule, AuditLog, Notification, NursePatientAssignment, PatientProfile, Role, SymptomDefinition,
                        SymptomForm, SymptomFormItem, SymptomRecord, User)
from app.models.base import utcnow
from app.seeds import skin_self_assessment as seed_data
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()
URL = "/api/v1/symptoms/records"
CODE = "rt_skin_self_assessment"
DAILY = "rt_daily_report"
EXPECTED = [  # (code, section, question, options in order, required)
    ("rt_dermatitis_redness", "過去 24 小時症狀自主管理", "放射線皮膚炎－發紅情形", ["無", "淺紅、粉紅", "深紅"], False),
    ("rt_dermatitis_desquamation", "過去 24 小時症狀自主管理", "放射線皮膚炎－脫皮、脫屑情形",
     ["無", "乾燥、有脫皮脫屑", "潮濕、有脫皮脫屑", "有脫皮脫屑伴出血"], False),
    ("rt_pain_skin", "過去 24 小時症狀自主管理", "疼痛－放射線皮膚發紅、脫皮導致", None, False),
    ("skin_itch", "皮膚狀況自我評估", "搔癢", ["有", "沒有"], False),
    ("skin_broken_exudate", "皮膚狀況自我評估", "破皮或有滲液", ["有", "沒有"], False),
    ("skin_assist_request", "皮膚狀況自我評估", "不確定自己的狀況，請護理師協助評估", ["是"], False),
]


def token(email, password="Demo@1234"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password}).get_json()["data"]["access_token"]


def H(t, key=None):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": key or str(uuid.uuid4())}


def values(**answers):
    return [{"definition_code": k, ("value_numeric" if isinstance(v, int) else "option_code"): v} for k, v in answers.items()]


def submit(t, pid="me", key=None, notes=None, form=CODE, **answers):
    body = {"patient_id": pid, "form_code": form, "values": values(**answers)}
    if notes is not None:
        body["notes"] = notes
    r = c.post(URL, json=body, headers=H(t, key))
    return r, (r.get_json() or {})


def daily_answers():
    return dict(rt_pain_skin=2, rt_pain_oral=1, rt_dermatitis_redness="light", rt_dermatitis_desquamation="dry", rt_appetite_poor=1,
                rt_fatigue=2, self_care_moisturizer="yes", self_care_towel_pat="yes", self_care_mouth_rinse="yes")


def taipei_iso(days_ahead, hour=9):
    d = datetime.now(timezone(timedelta(hours=8))).date() + timedelta(days=days_ahead)
    return f"{d.isoformat()}T{hour:02d}:00:00+08:00"


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
    pt, p2t, nt, nt2, at = (token(e) for e in ("patient01@demo.local", "p2@demo.local", "nurse01@demo.local", "nurse02@demo.local",
                                               "admin01@demo.local"))
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    pid = p1.public_id

    # ================================================================ the form
    daily_before = c.get(f"/api/v1/symptoms/forms/{DAILY}", headers=H(pt)).get_json()["data"]
    r = c.get(f"/api/v1/symptoms/forms/{CODE}", headers=H(pt))
    form = r.get_json()["data"]
    check("GET form → 200, name 放射治療皮膚反應自我評估", r.status_code == 200 and form["name"] == "放射治療皮膚反應自我評估", form.get("name"))
    got = [(i["definition"]["code"], i["definition"]["category"]["name_zh"], i["definition"]["question_text"],
            [o["label_zh"] for o in i["definition"]["options"]] if "options" in i["definition"] else None, i["is_required"])
           for i in form["items"]]
    check("questions, sections, texts, option order; every question optional", got == EXPECTED, got)
    daily_ids = {i["definition"]["code"]: i["definition"]["id"] for i in daily_before["items"]}
    skin_ids = {i["definition"]["code"]: i["definition"]["id"] for i in form["items"]}
    check("reuses the rt_daily_report questions themselves (same definitions, not copies)",
          all(skin_ids[k] == daily_ids[k] for k in seed_data.REUSED))
    check("rt_daily_report unchanged: 9 questions, all required, same order",
          [i["definition"]["code"] for i in daily_before["items"]] == ["rt_pain_skin", "rt_pain_oral", "rt_dermatitis_redness",
                                                                      "rt_dermatitis_desquamation", "rt_appetite_poor", "rt_fatigue",
                                                                      "self_care_moisturizer", "self_care_towel_pat", "self_care_mouth_rinse"]
          and all(i["is_required"] for i in daily_before["items"]))
    patient_text = json.dumps(form, ensure_ascii=False)
    check("no grade / CTCAE / RTOG wording in what the patient sees", not any(w in patient_text for w in ("Grade", "grade", "CTCAE", "RTOG", "分級")))
    form_def_ids = [d.id for d in db.session.query(SymptomDefinition).filter(SymptomDefinition.code.in_([e[0] for e in EXPECTED]))]
    check("no alert rule points at any question of the form (thresholds pending clinical review)",
          db.session.query(AlertRule).filter(AlertRule.symptom_definition_id.in_(form_def_ids)).count() == 0)

    # ================================================================ submit
    before_alerts = db.session.query(Notification).count()
    key = str(uuid.uuid4())
    r, body = submit(pt, key=key, notes="  右側頸部照射處，晚上比較癢  ", rt_dermatitis_redness="light", rt_dermatitis_desquamation="dry",
                     rt_pain_skin=3, skin_itch="yes", skin_broken_exudate="no")
    d = body.get("data", {})
    check("patient submits → 201, stored as a symptom record of the form (patient_app, submitted)", r.status_code == 201
          and d["form"]["code"] == CODE and d["source"] == "patient_app" and d["review_status"] == "submitted", body)
    check("supplementary notes stored (trimmed)", d.get("notes") == "右側頸部照射處，晚上比較癢")
    vals = {v["definition_code"]: v for v in d["values"]}
    check("answers carry their labels (搔癢：有, 發紅情形：淺紅、粉紅, 疼痛 3)", vals["skin_itch"]["option_label"] == "有"
          and vals["rt_dermatitis_redness"]["option_label"] == "淺紅、粉紅" and vals["rt_pain_skin"]["value_numeric"] == 3)
    check("no alert raised (no alert rule for this form)", d["triggered_alerts"] == [] and db.session.query(Notification).count() == before_alerts)
    rec_id = d["id"]
    r2, b2 = submit(pt, key=key, notes="  右側頸部照射處，晚上比較癢  ", rt_dermatitis_redness="light", rt_dermatitis_desquamation="dry",
                    rt_pain_skin=3, skin_itch="yes", skin_broken_exudate="no")
    check("same Idempotency-Key → same record (a retry never creates a second one)", r2.status_code == 201 and b2["data"]["id"] == rec_id)

    r, body = submit(pt, skin_assist_request="yes")
    check("「不確定，請護理師協助評估」 alone → 201 (需要護理師協助評估：是)", r.status_code == 201
          and body["data"]["values"][0]["label"] == "需要護理師協助評估" and body["data"]["values"][0]["option_label"] == "是", body)
    unsure_id = body["data"]["id"]
    r, body = submit(pt, rt_dermatitis_redness="dark")
    check("not once per day: a third report the same day → 201", r.status_code == 201)
    third_id = body["data"]["id"]

    # validation
    r = c.post(URL, json={"patient_id": "me", "form_code": CODE, "values": []}, headers=H(pt))
    check("no answer at all → 400", r.status_code == 400)
    r, body = submit(pt, skin_itch="sometimes")
    check("unknown option → 400", r.status_code == 400 and "option_code" in json.dumps(body))
    r, body = submit(pt, skin_assist_request="no")
    check("the request has one option only (「需要」) → other codes 400", r.status_code == 400)
    r, body = submit(pt, rt_fatigue=3)
    check("a daily-report question that is not part of this form → 400", r.status_code == 400 and "is not part of form" in json.dumps(body))
    r, body = submit(pt, notes="癢" * 2001, skin_itch="yes")
    check("notes longer than 2000 characters → 400", r.status_code == 400)
    r, body = submit(pt, rt_pain_skin=11)
    check("0–10 pain out of range → 400", r.status_code == 400)
    r = c.post(URL, json={"patient_id": "me", "form_code": CODE, "values": values(skin_itch="yes")}, headers={"Authorization": f"Bearer {pt}"})
    check("patient without Idempotency-Key → 428 (existing rule)", r.status_code == 428)

    # ================================================================ the daily report and the home page are not affected
    w = c.get("/api/v1/dashboard/patient/me", headers=H(pt)).get_json()["data"]["widgets"]
    check("今日已回報 stays false after skin self-assessments (daily report not done)", w["daily-report"]["reported_today"] is False, w["daily-report"])
    stamps = w["self-care-stamps"]
    check("self-care stamps unaffected (no answer today)", all(day["answer"] is None for item in stamps["items"] for day in item["days"]
                                                              if day["date"] == w["daily-report"]["date"]))
    keys = [s["key"] for s in w["symptom-trend"]["series"]]
    check("home chart: no line for the skin form's own yes / no questions", not {"skin_itch", "skin_broken_exudate", "skin_assist_request"} & set(keys), keys)
    pain = next((s for s in w["symptom-trend"]["series"] if s["key"] == "rt_pain_skin"), None)
    check("…the reused 0–10 skin pain question keeps its line (same question)", pain is not None and pain["points"][-1]["value"] == 3,
          pain["points"][-1] if pain else keys)
    r, body = submit(pt, form=DAILY, **daily_answers())
    check("the daily report can still be sent the same day → 201 (not 409)", r.status_code == 201, body.get("error"))
    r, body = submit(pt, form=DAILY, **daily_answers())
    check("…and its once-per-day rule still works → 409", r.status_code == 409 and body["error"]["code"] == "ALREADY_REPORTED_TODAY")

    # ================================================================ patient history
    mine = c.get(f"{URL}/me?form_code={CODE}", headers=H(pt)).get_json()
    check("patient lists own skin self-assessments, newest first", mine["meta"]["total"] == 3 and mine["data"][0]["id"] == third_id
          and all(x["form"]["code"] == CODE for x in mine["data"]), mine["meta"])
    check("each entry keeps its time of creation", all(x["recorded_at"] for x in mine["data"]))
    check("patient cannot list another patient's records → 404", c.get(f"{URL}/{p2.public_id}?form_code={CODE}", headers=H(pt)).status_code == 404)

    # ================================================================ nurse: queue, history, review
    pending = c.get("/api/v1/dashboard/widgets/pending-symptom-reviews/data", headers=H(nt)).get_json()["data"]
    items = {x["id"]: x for x in pending}
    check("all three are in the nurse's pending queue, with the form name", all(i in items for i in (rec_id, unsure_id, third_id))
          and items[rec_id]["form"] == {"code": CODE, "name": "放射治療皮膚反應自我評估"}, [x.get("form") for x in pending])
    check("queue summary shows the 「不確定」 request", "需要護理師協助評估：是" in items[unsure_id]["summary"], items[unsure_id]["summary"])
    check("daily report items carry their form too (additive field)", any(x["form"] and x["form"]["code"] == DAILY for x in pending))
    nv = c.get(f"/api/v1/dashboard/patient/{pid}", headers=H(nt)).get_json()["data"]["widgets"]["nurse-view"]
    check("nurse-view pending list carries the form as well", any(x.get("form", {}) and x["form"]["code"] == CODE for x in nv["pending_symptom_reviews"]["items"]))

    lst = c.get(f"{URL}/{pid}?form_code={CODE}", headers=H(nt)).get_json()
    first = next(x for x in lst["data"] if x["id"] == rec_id)
    check("nurse sees the patient's answers, notes and reporter", first["notes"] == "右側頸部照射處，晚上比較癢" and first["reported_by"]
          and len(first["values"]) == 5)
    check("unassigned nurse → 404 (list and review)", c.get(f"{URL}/{pid}?form_code={CODE}", headers=H(nt2)).status_code == 404
          and c.post(f"{URL}/{rec_id}/review", json={"action_note": "x"}, headers=H(nt2)).status_code == 404)
    check("admin cannot review → 403", c.post(f"{URL}/{rec_id}/review", json={"action_note": "x"}, headers=H(at)).status_code == 403)
    check("patient cannot review → 403", c.post(f"{URL}/{rec_id}/review", json={"action_note": "x"}, headers=H(pt)).status_code == 403)

    # follow-up validation: nothing changes on a rejected review
    for label, when in (("in the past", taipei_iso(-2)), ("more than 180 days ahead", taipei_iso(200)), ("without a timezone", "2026-12-01T09:00:00")):
        r = c.post(f"{URL}/{rec_id}/review", json={"action_note": "追蹤", "next_follow_up_at": when}, headers=H(nt))
        check(f"follow-up date {label} → 400", r.status_code == 400 and "next_follow_up_at" in json.dumps(r.get_json()), r.get_json())
    check("…the record is still waiting for review", db.session.get(SymptomRecord, rec_id).review_status == "submitted")

    rec = db.session.get(SymptomRecord, rec_id)
    answers_before = sorted((v.definition.code, v.option_id, v.value_numeric, v.score) for v in rec.values)
    notes_before, recorded_before = rec.notes, rec.recorded_at
    audits_before = db.session.query(AuditLog).count()
    follow = taipei_iso(3)
    r = c.post(f"{URL}/{rec_id}/review", json={"action_note": "已電話衛教保濕與避免摩擦，三天後回診時評估", "assessment_type": "phone_follow_up",
                                              "next_follow_up_at": follow}, headers=H(nt))
    body = r.get_json()
    check("nurse reviews with the existing flow + a follow-up date → 200", r.status_code == 200, body)
    db.session.expire_all()
    rec = db.session.get(SymptomRecord, rec_id)
    a = rec.nursing_assessment
    check("review stored as a signed nursing assessment (reviewer, time, follow-up date)", rec.review_status == "reviewed"
          and rec.reviewed_by is not None and rec.reviewed_at is not None and a is not None and a.sign_status == "signed"
          and a.next_follow_up_at is not None and a.plan.startswith("已電話衛教"))
    check("the patient's answers, notes and time are not overwritten", sorted((v.definition.code, v.option_id, v.value_numeric, v.score)
                                                                               for v in rec.values) == answers_before
          and rec.notes == notes_before and rec.recorded_at == recorded_before)
    check("no professional grade written to the patient's answers", all(v.ctcae_grade is None for v in rec.values))
    check("review payload (nurse) carries next_follow_up_at", body["data"]["review"]["next_follow_up_at"] is not None)
    new_audits = db.session.query(AuditLog).filter(AuditLog.id > 0).order_by(AuditLog.id.desc()).limit(db.session.query(AuditLog).count() - audits_before).all()
    actions = {(x.action, x.resource_type) for x in new_audits}
    created = next((x for x in new_audits if x.resource_type == "nursing_assessments"), None)
    check("audited: CREATE nursing_assessments (with the follow-up) + UPDATE symptom_records",
          ("CREATE", "nursing_assessments") in actions and ("UPDATE", "symptom_records") in actions
          and created is not None and "next_follow_up_at" in (created.changes or {}), actions)
    r = c.post(f"{URL}/{rec_id}/review", json={"action_note": "再審一次"}, headers=H(nt))
    check("a second review → 422 (never overwrites the first)", r.status_code == 422)
    r = c.post(f"{URL}/{unsure_id}/review", json={"action_note": "已電話了解狀況，皮膚完整"}, headers=H(nt))
    check("review without a follow-up → 200", r.status_code == 200 and r.get_json()["data"]["review"]["next_follow_up_at"] is None)

    fu = c.get(f"{URL}/{pid}?form_code={CODE}&review_status=follow_up", headers=H(nt)).get_json()
    check("review_status=follow_up (需追蹤) lists only reviewed records with a follow-up", [x["id"] for x in fu["data"]] == [rec_id], fu["data"])
    check("meta counts: submitted 1 / reviewed 2 / follow_up 1", (fu["meta"]["submitted"], fu["meta"]["reviewed"], fu["meta"]["follow_up"]) == (1, 2, 1),
          fu["meta"])
    rv = c.get(f"{URL}/{pid}?form_code={CODE}&review_status=reviewed", headers=H(nt)).get_json()
    check("reviewed still lists both reviewed records", {x["id"] for x in rv["data"]} == {rec_id, unsure_id})
    check("unknown review_status → 400", c.get(f"{URL}/{pid}?review_status=later", headers=H(nt)).status_code == 400)

    mine = c.get(f"{URL}/me?form_code={CODE}", headers=H(pt)).get_json()
    reviewed_entry = next(x for x in mine["data"] if x["id"] == rec_id)
    check("patient: reviewed state only — no nurse note, no reviewer, no follow-up date", reviewed_entry["review_status"] == "reviewed"
          and reviewed_entry["review"] is None and reviewed_entry["reviewed_by"] is None)
    check("patient: no professional grade anywhere in the payload", "ctcae" not in json.dumps(mine).lower())
    check("patient: the 需追蹤 filter is staff only → 403, and no follow_up count in meta",
          c.get(f"{URL}/me?review_status=follow_up", headers=H(pt)).status_code == 403 and "follow_up" not in mine["meta"])

    # ended assignment → no access
    assignment = db.session.query(NursePatientAssignment).filter_by(patient_id=p1.id, ended_at=None).first()
    assignment.ended_at = utcnow()
    db.session.commit()
    check("assignment ended → the nurse gets 404 at once (list and review)", c.get(f"{URL}/{pid}?form_code={CODE}", headers=H(nt)).status_code == 404
          and c.post(f"{URL}/{third_id}/review", json={"action_note": "x"}, headers=H(nt)).status_code == 404)
    assignment.ended_at = None
    db.session.commit()

# ================================================================ timeline text (choice answers) and answer order
with app.app_context():
    names = {d.code: d.name_zh for d in db.session.query(SymptomDefinition)}
    form_order = [e[0] for e in EXPECTED]

    def tl_event(t, rid, who="me"):
        evs = c.get(f"/api/v1/patients/{who}/timeline?limit=100", headers=H(t)).get_json()["data"]
        return next((e for e in evs if e["event_id"] == f"SYMPTOM:{rid}"), None) or {"summary": "", "detail": {"values": []}}

    e = tl_event(pt, rec_id)
    s = e["summary"]
    detail = {v["code"]: v for v in e["detail"]["values"]}
    check("timeline (patient): choice answers in the summary show their option text", "放射線皮膚炎－發紅情形：淺紅、粉紅" in s, s)
    check("…0–10 answers keep their number (疼痛…皮膚 3)", "疼痛－放射線皮膚發紅、脫皮導致 3" in s, s)
    check("…never a bare option score (no 「發紅情形 1」 / 「搔癢 1」)", "發紅情形 1" not in s and "搔癢 1" not in s, s)
    check("timeline detail: 搔癢 → 有 (option text, option_code, no score → no 「/ 10」)", detail.get("skin_itch", {}).get("value") == "有"
          and detail["skin_itch"].get("option_code") == "yes" and "score" not in detail["skin_itch"], detail.get("skin_itch"))
    check("timeline detail: 破皮或有滲液 → 沒有; 發紅情形 → 淺紅、粉紅", detail.get("skin_broken_exudate", {}).get("value") == "沒有"
          and detail.get("rt_dermatitis_redness", {}).get("value") == "淺紅、粉紅")
    check("timeline detail: the 0–10 answer keeps value + score", detail.get("rt_pain_skin", {}).get("value") == "3" and detail["rt_pain_skin"].get("score") == 3)
    check("timeline detail: answers in the form's question order", [v["code"] for v in e["detail"]["values"]] == [c_ for c_ in form_order if c_ in detail],
          [v["code"] for v in e["detail"]["values"]])
    check("timeline: 「需要護理師協助評估：是」 (patient and nurse)", tl_event(pt, unsure_id)["summary"] == "需要護理師協助評估：是"
          and tl_event(nt, unsure_id, pid)["summary"] == "需要護理師協助評估：是", tl_event(pt, unsure_id)["summary"])

    daily_id = c.get(f"{URL}/me?form_code={DAILY}", headers=H(pt)).get_json()["data"][0]["id"]
    dd = {v["code"]: v for v in tl_event(pt, daily_id)["detail"]["values"]}
    check("daily report in the timeline: 有／沒有 questions show 有, the dermatitis question its option text",
          dd.get("self_care_moisturizer", {}).get("value") == "有" and dd.get("rt_dermatitis_redness", {}).get("value") == "淺紅、粉紅"
          and "score" not in dd.get("self_care_moisturizer", {"score": 1}))
    check("daily report: 0–10 answers unchanged (疲倦 2 / 10)", dd.get("rt_fatigue", {}).get("value") == "2" and dd["rt_fatigue"].get("score") == 2)

    r = c.post(URL, json={"patient_id": "me", "form_code": "daily_chemo_check", "values": [
        {"definition_code": "fever", "value_boolean": True}, {"definition_code": "fatigue", "value_numeric": 1},
        {"definition_code": "nausea", "value_numeric": 2}, {"definition_code": "pain", "value_numeric": 8}]}, headers=H(pt))
    chemo = tl_event(pt, r.get_json()["data"]["id"])
    expected = f"{names['pain']} 8、{names['nausea']} 2、{names['fatigue']} 1、{names['fever']}"
    check("existing numeric / boolean form unchanged: summary 「疼痛 8、噁心 2、疲倦 1、發燒或畏寒」, critical", chemo["summary"] == expected
          and chemo.get("severity") == "critical", chemo["summary"])
    cd = {v["code"]: v for v in chemo["detail"]["values"]}
    check("…boolean detail 有 (no option_code), numeric detail with score", cd["fever"]["value"] == "有" and "option_code" not in cd["fever"]
          and cd["pain"]["value"] == "8" and cd["pain"]["score"] == 8)
    check("…answers in the form's question order, not the order sent", [v["code"] for v in chemo["detail"]["values"]] == ["pain", "nausea", "fatigue", "fever"])

    # API: answers in the form's question order, whatever order they were sent in
    reversed_values = list(reversed(values(rt_dermatitis_redness="dark", rt_dermatitis_desquamation="moist", rt_pain_skin=5,
                                          skin_itch="no", skin_broken_exudate="yes", skin_assist_request="yes")))
    r = c.post(URL, json={"patient_id": "me", "form_code": CODE, "values": reversed_values}, headers=H(pt))
    created = r.get_json()["data"]
    check("POST response: answers in question order (sent reversed)", [v["definition_code"] for v in created["values"]] == form_order,
          [v["definition_code"] for v in created["values"]])
    listed = next(x for x in c.get(f"{URL}/{pid}?form_code={CODE}", headers=H(nt)).get_json()["data"] if x["id"] == created["id"])
    check("GET list (nurse): same order", [v["definition_code"] for v in listed["values"]] == form_order)
    mine_listed = next(x for x in c.get(f"{URL}/me?form_code={CODE}", headers=H(pt)).get_json()["data"] if x["id"] == created["id"])
    check("GET list (patient): same order", [v["definition_code"] for v in mine_listed["values"]] == form_order)
    check("response structure unchanged: value keys per type", {k for k in created["values"][0]} == {"definition_code", "label", "option_code", "option_label", "score"}
          and {k for k in created["values"][2]} == {"definition_code", "label", "value_numeric", "score"}, [sorted(v) for v in created["values"][:3]])
    reviewed = c.post(f"{URL}/{created['id']}/review", json={"action_note": "已了解"}, headers=H(nt)).get_json()["data"]
    check("review response: same order", [v["definition_code"] for v in reviewed["values"]] == form_order)
    subjective = db.session.get(SymptomRecord, created["id"]).nursing_assessment.subjective
    check("review summary (nursing assessment) lists answers in question order",
          subjective.index("發紅情形") < subjective.index("脫皮") < subjective.index("疼痛") < subjective.index("破皮"), subjective)

    daily_order = ["rt_pain_skin", "rt_pain_oral", "rt_dermatitis_redness", "rt_dermatitis_desquamation", "rt_appetite_poor", "rt_fatigue",
                   "self_care_moisturizer", "self_care_towel_pat", "self_care_mouth_rinse"]
    r = c.post(URL, json={"patient_id": "me", "form_code": DAILY, "values": list(reversed(values(**daily_answers())))}, headers=H(p2t))
    d2 = r.get_json()["data"]
    check("daily report sent in reverse → stored and returned in its question order", r.status_code == 201
          and [v["definition_code"] for v in d2["values"]] == daily_order, [v["definition_code"] for v in d2.get("values", [])])
    r = c.post(f"{URL}/{d2['id']}/amend", json={"amend_reason": "病人來電更正", "values": list(reversed(values(**{**daily_answers(), "rt_fatigue": 5})))},
               headers=H(nt2))
    amended = r.get_json()["data"]
    check("correction (amend) of it → also question order", r.status_code == 201 and [v["definition_code"] for v in amended["values"]] == daily_order,
          r.get_json())
    hist = c.get(f"{URL}/{amended['id']}/history", headers=H(nt2)).get_json()["data"]
    check("correction history → question order in every version (2 versions)", len(hist) == 2
          and all([v["definition_code"] for v in ver["values"]] == daily_order for ver in hist), json.dumps(hist, ensure_ascii=False)[:300])


# ================================================================ data migration = seed; never touches rt_daily_report
migrations = str(Path(__file__).resolve().parents[1] / "migrations")


def snapshot(form_code):
    from sqlalchemy import text
    rows = db.session.execute(text(
        "SELECT d.code, c.code, c.name_zh, d.name_zh, d.question_text, d.value_type, d.min_value, d.max_value, d.step,"
        " d.higher_is_worse, i.display_order, i.is_required FROM symptom_form_items i JOIN symptom_forms f ON f.id = i.form_id"
        " JOIN symptom_definitions d ON d.id = i.definition_id JOIN symptom_categories c ON c.id = d.category_id"
        " WHERE f.code = :f ORDER BY i.display_order"), {"f": form_code}).all()
    options = db.session.execute(text(
        "SELECT d.code, o.value_code, o.label_zh, o.score, o.display_order FROM symptom_definition_options o"
        " JOIN symptom_definitions d ON d.id = o.definition_id JOIN symptom_form_items i ON i.definition_id = d.id"
        " JOIN symptom_forms f ON f.id = i.form_id WHERE f.code = :f ORDER BY d.code, o.display_order"), {"f": form_code}).all()
    form = db.session.execute(text("SELECT name, intended_for, recall_period_hours, availability, version, is_active"
                                    " FROM symptom_forms WHERE code = :f"), {"f": form_code}).first()
    norm = lambda r: tuple(float(x) if hasattr(x, "as_tuple") else (bool(x) if isinstance(x, bool) else x) for x in r)
    return [norm(r) for r in rows], [norm(r) for r in options], tuple(form) if form else None


for label, existing in (("empty database", False), ("existing database", True)):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        from app.config import DevelopmentConfig
        from flask_migrate import downgrade, upgrade
        from sqlalchemy import text

        target = f"sqlite:///{Path(tmp, 'mig.db').as_posix()}"
        # a separate app on a throw-away SQLite file (the engine is built at app creation, from the class attribute)
        saved = (DevelopmentConfig.SQLALCHEMY_DATABASE_URI, DevelopmentConfig.EMAIL_CAPTURE_DIR)
        DevelopmentConfig.SQLALCHEMY_DATABASE_URI = target
        DevelopmentConfig.EMAIL_CAPTURE_DIR = None
        mig = create_app("development")
        DevelopmentConfig.SQLALCHEMY_DATABASE_URI, DevelopmentConfig.EMAIL_CAPTURE_DIR = saved
        with mig.app_context(), warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # migrations/env.py uses Flask-SQLAlchemy's get_engine()
            if str(db.engine.url) != target:
                raise SystemExit(f"refusing to migrate {db.engine.url}: not the throw-away database")
            if existing:
                upgrade(directory=migrations, revision="b5cd01c11e1d")  # chat head = what is deployed / about to be
                seed_dev_data(); db.session.commit()
                counts_before = [db.session.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
                                 for t in ("users", "patient_profiles", "nurse_patient_assignments", "symptom_records")]
                daily_before = snapshot(DAILY)
                db.session.execute(text("DELETE FROM symptom_form_items WHERE form_id = (SELECT id FROM symptom_forms WHERE code = :f)"), {"f": CODE})
                db.session.execute(text("DELETE FROM symptom_forms WHERE code = :f"), {"f": CODE})
                for code, _n, _t, _o in seed_data.NEW_DEFINITIONS:  # as on the real database: the seed has never run there
                    db.session.execute(text("DELETE FROM symptom_definition_options WHERE definition_id = (SELECT id FROM symptom_definitions WHERE code = :c)"), {"c": code})
                    db.session.execute(text("DELETE FROM symptom_definitions WHERE code = :c"), {"c": code})
                db.session.execute(text("DELETE FROM symptom_categories WHERE code = 'skin_self_assessment'"))
                db.session.commit()
            upgrade(directory=migrations)
            migrated = snapshot(CODE)
            check(f"migration ({label}): creates the form, 6 items (3 reused), 3 new questions",
                  len(migrated[0]) == 6 and [r[0] for r in migrated[0]] == [e[0] for e in EXPECTED] and migrated[2] is not None, migrated[0][:2])
            if existing:
                check(f"migration ({label}): rt_daily_report identical before / after", snapshot(DAILY) == daily_before)
                counts_after = [db.session.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
                                for t in ("users", "patient_profiles", "nurse_patient_assignments", "symptom_records")]
                check(f"migration ({label}): users, patients, assignments, records untouched", counts_after == counts_before, (counts_before, counts_after))
            seed_dev_data(); db.session.commit()
            check(f"migration ({label}): identical to what the seed defines (form, questions, options)", snapshot(CODE) == migrated)
            downgrade(directory=migrations, revision="b5cd01c11e1d")
            gone = db.session.execute(text("SELECT COUNT(*) FROM symptom_forms WHERE code = :f"), {"f": CODE}).scalar()
            new_left = db.session.execute(text("SELECT COUNT(*) FROM symptom_definitions WHERE code LIKE 'skin_%'")).scalar()
            check(f"migration ({label}): downgrade without reports removes only this form and its new questions",
                  gone == 0 and new_left == 0 and len(snapshot(DAILY)[0]) == 9)
            upgrade(directory=migrations)
            user_id = db.session.execute(text("SELECT id FROM users WHERE email = 'patient01@demo.local'")).scalar()
            patient_id = db.session.execute(text("SELECT id FROM patient_profiles WHERE patient_code = 'P00001'")).scalar()
            form_id = db.session.execute(text("SELECT id FROM symptom_forms WHERE code = :f"), {"f": CODE}).scalar()
            now = utcnow()
            db.session.execute(text("INSERT INTO symptom_records (patient_id, form_id, form_version, recorded_at, reported_by, review_status,"
                                    " source, record_status, created_at, updated_at) VALUES (:p, :f, 1, :n, :u, 'submitted', 'patient_app',"
                                    " 'final', :n, :n)"), {"p": patient_id, "f": form_id, "n": now, "u": user_id})
            db.session.commit()
            downgrade(directory=migrations, revision="b5cd01c11e1d")
            kept = db.session.execute(text("SELECT COUNT(*) FROM symptom_forms WHERE code = :f"), {"f": CODE}).scalar()
            check(f"migration ({label}): downgrade with a report present keeps the form", kept == 1)
            db.session.remove()
            db.engine.dispose()  # Windows: close the SQLite file before the temp directory is removed

# literal-data parity without importing the migration as a package
mig_source = (Path(__file__).resolve().parents[1] / "migrations" / "versions" / "c3e9a1f4b7d2_skin_self_assessment_form.py").read_text(encoding="utf-8")
ns = {}
exec(compile(mig_source.split("def _now():")[0].replace("from alembic import op", "").replace("import sqlalchemy as sa", ""), "migration", "exec"), ns)
check("migration literal data == seed module data", ns["NEW_DEFINITIONS"] == seed_data.NEW_DEFINITIONS and ns["CATEGORY"] == seed_data.CATEGORY
      and ns["ITEMS"] == seed_data.ITEMS and ns["REUSED"] == seed_data.REUSED and ns["FORM_NAME"] == seed_data.FORM["name"]
      and ns["FORM_CODE"] == seed_data.FORM_CODE)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
