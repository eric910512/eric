"""Patient home redesign (需求列表 2026-10): the daily report entry (今日已回報 by the existing
once-per-day rule), the self-care stamp cards (current month in the patient's timezone; 有 / 沒有 /
not reported kept apart), the trend without the self-care answers, 放射線治療進度 from radiotherapy
appointments, the home layout, the nurse 處理項目 notes staying internal, and access control
(patients see only themselves, nurses only assigned patients — enforced by the backend).

"Now" is pinned by replacing ``utcnow`` in the symptom and dashboard services, so month lengths and
the local-midnight boundary (Asia/Taipei = UTC+8) are tested on fixed dates."""
import sys
import uuid
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import Appointment, Notification, NursePatientAssignment, PatientProfile, Role, User
from app.models.base import utcnow
from app.modules.dashboard import services as dashboard_services
from app.modules.dashboard.layouts import PATIENT_HOME_LAYOUT
from app.modules.symptom import services as symptom_services
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()
FORM = "rt_daily_report"
SELF_CARE = ["self_care_moisturizer", "self_care_towel_pat", "self_care_mouth_rinse"]
NOW = {"value": None}


def pin_now(value):
    """Pin "now" (naive UTC) for the symptom + dashboard services; None = the real clock."""
    NOW["value"] = value


def _now():
    return NOW["value"] or utcnow()


symptom_services.utcnow = _now
dashboard_services.utcnow = _now


def token(email):
    return c.post("/api/v1/auth/login", json={"email": email, "password": "Demo@1234"}).get_json()["data"]["access_token"]


def H(t):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": str(uuid.uuid4())}


def report(t, at=None, pid="me", **self_care):
    values = {"rt_pain_skin": 3, "rt_pain_oral": 2, "rt_dermatitis_redness": "light", "rt_dermatitis_desquamation": "none",
              "rt_appetite_poor": 1, "rt_fatigue": 4, "self_care_moisturizer": "yes", "self_care_towel_pat": "yes",
              "self_care_mouth_rinse": "yes", **self_care}
    body = {"patient_id": pid, "form_code": FORM,
            "values": [{"definition_code": k, ("value_numeric" if isinstance(v, int) else "option_code"): v} for k, v in values.items()]}
    if at:
        body["recorded_at"] = at
    r = c.post("/api/v1/symptoms/records", json=body, headers=H(t))
    return r.status_code, (r.get_json() or {})


def widgets(t, pid="me"):
    r = c.get(f"/api/v1/dashboard/patient/{pid}", headers={"Authorization": f"Bearer {t}"})
    return r.status_code, (r.get_json() or {}).get("data", {}).get("widgets", {})


def day_answers(stamps, day):
    """{code: answer} for one date of the stamp cards."""
    return {i["code"]: next(d["answer"] for d in i["days"] if d["date"] == day) for i in stamps["items"]}


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
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    check("patients are in Asia/Taipei (UTC+8, the boundary below)", p1.timezone == "Asia/Taipei" and p2.timezone == "Asia/Taipei")

    # ================================================================ layout
    pinned = [i["widget_code"] for i in PATIENT_HOME_LAYOUT["pinned"]]
    items = [i["widget_code"] for i in PATIENT_HOME_LAYOUT["items"]]
    check("daily report entry is the first thing on the home page", pinned[0] == "daily-report")
    check("radiation progress → today's schedule come first; trend is last", items[:2] == ["radiation-progress", "today-schedule"]
          and items[-1] == "symptom-trend")
    check("今天的症狀 / 我的通知 are not on the home layout", "symptom-quick-report" not in items + pinned and "my-notifications" not in items + pinned)

    # ================================================================ February (28 days), before any report
    pin_now(datetime(2026, 2, 27, 15, 30))  # 2026-02-27 23:30 in Taipei
    status, w = widgets(pt)
    stamps, entry = w["self-care-stamps"], w["daily-report"]
    check("dashboard 200 for the patient", status == 200)
    check("daily-report: not reported yet", entry == {"form": {"code": FORM, "name": "每日症狀與自我照護回報"}, "date": "2026-02-27",
                                                      "reported_today": False, "today_record_id": None}, entry)
    check("stamps: month = the patient's local month, 28 days for February 2026",
          stamps["month"] == "2026-02" and stamps["from"] == "2026-02-01" and stamps["to"] == "2026-02-28"
          and all(len(i["days"]) == 28 for i in stamps["items"]), stamps["month"])
    check("stamps: the three self-care questions in form order, with the form's question text",
          [i["code"] for i in stamps["items"]] == SELF_CARE
          and stamps["items"][0]["label"] == "我今天擦保濕乳液或醫師開的藥膏了嗎？", [i["code"] for i in stamps["items"]])
    check("stamps: nothing reported → every day null (never 'no')",
          all(d["answer"] is None for i in stamps["items"] for d in i["days"]))

    # report for yesterday 23:59:59 local (15:59:59Z) — counts for 2/26, not 2/27
    s, b = report(pt, at="2026-02-26T15:59:59Z", self_care_towel_pat="no")
    check("report for 2/26 23:59:59 local → 201", s == 201, b)
    _, w = widgets(pt)
    check("2/26: 有 / 沒有 / 有 as answered", day_answers(w["self-care-stamps"], "2026-02-26")
          == {"self_care_moisturizer": "yes", "self_care_towel_pat": "no", "self_care_mouth_rinse": "yes"})
    check("2/27 (today, not reported) stays null — not 'no'", set(day_answers(w["self-care-stamps"], "2026-02-27").values()) == {None})
    check("yesterday's report does not make today 已回報", w["daily-report"]["reported_today"] is False)

    # an other-form report (今天的症狀, daily_chemo_check) does not count as the daily report
    r = c.post("/api/v1/symptoms/records", headers=H(pt), json={"patient_id": "me", "form_code": "daily_chemo_check", "values": [
        {"definition_code": "pain", "value_numeric": 1}, {"definition_code": "nausea", "value_numeric": 0},
        {"definition_code": "fatigue", "value_numeric": 2}, {"definition_code": "fever", "value_boolean": False}]})
    _, w = widgets(pt)
    check("其他症狀回報 (daily_chemo_check) does not mark 每日回報 as done", r.status_code == 201 and w["daily-report"]["reported_today"] is False)

    s, b = report(pt, self_care_moisturizer="no", self_care_mouth_rinse="no")
    today_id = b.get("data", {}).get("id")
    _, w = widgets(pt)
    check("after today's report → 今日已回報 with that record", s == 201 and w["daily-report"]["reported_today"] is True
          and w["daily-report"]["today_record_id"] == today_id, w["daily-report"])
    check("today's stamps show the new answers right away", day_answers(w["self-care-stamps"], "2026-02-27")
          == {"self_care_moisturizer": "no", "self_care_towel_pat": "yes", "self_care_mouth_rinse": "no"})
    s, b = report(pt)
    check("existing once-per-day rule unchanged: a second report today → 409", s == 409 and b["error"]["code"] == "ALREADY_REPORTED_TODAY")
    check("other days of the month are still null", all(d["answer"] is None for i in w["self-care-stamps"]["items"]
                                                          for d in i["days"] if d["date"] not in ("2026-02-26", "2026-02-27")))

    # ================================================================ trend: scores only
    keys = [s["key"] for s in w["symptom-trend"]["series"]]
    check("trend has the 0–10 daily-report symptoms (e.g. rt_pain_skin)", "rt_pain_skin" in keys and "pain" in keys, keys)
    check("trend has none of the three self-care answers", not set(SELF_CARE) & set(keys), keys)
    _, nw = widgets(nt, p1.public_id)
    check("nurse's trend for the patient also leaves them out", not set(SELF_CARE) & {s["key"] for s in nw["symptom-trend"]["series"]})

    # ================================================================ local midnight → new day, new month (March, 31 days)
    pin_now(datetime(2026, 2, 28, 15, 59, 59))  # 2/28 23:59:59 local
    s, _ = report(pt)
    check("2/28 23:59:59 local is a new day → 201", s == 201)
    pin_now(datetime(2026, 2, 28, 16, 0, 30))  # 3/1 00:00:30 local
    _, w = widgets(pt)
    check("00:00 local starts a new day: not reported yet", w["daily-report"]["date"] == "2026-03-01" and w["daily-report"]["reported_today"] is False)
    check("new local month: March has 31 days, all null (February answers not carried over)",
          w["self-care-stamps"]["month"] == "2026-03" and all(len(i["days"]) == 31 for i in w["self-care-stamps"]["items"])
          and all(d["answer"] is None for i in w["self-care-stamps"]["items"] for d in i["days"]))
    s, _ = report(pt, at="2026-02-28T16:00:00Z", self_care_towel_pat="no")
    _, w = widgets(pt)
    check("a report at 16:00Z = 3/1 00:00 local lands on 3/1", s == 201 and w["daily-report"]["reported_today"] is True
          and day_answers(w["self-care-stamps"], "2026-03-01")["self_care_towel_pat"] == "no")
    pin_now(datetime(2026, 4, 10, 4, 0))
    _, w = widgets(pt)
    check("April has 30 days", w["self-care-stamps"]["month"] == "2026-04" and all(len(i["days"]) == 30 for i in w["self-care-stamps"]["items"]))

    # ================================================================ access control
    pin_now(datetime(2026, 3, 1, 4, 0))  # 3/1 12:00 local
    s2, w2 = widgets(p2t)
    check("patient B sees only their own (empty) stamp cards", s2 == 200 and w2["daily-report"]["reported_today"] is False
          and all(d["answer"] is None for i in w2["self-care-stamps"]["items"] for d in i["days"]))
    check("patient B cannot load patient A's dashboard → 404", widgets(p2t, p1.public_id)[0] == 404)
    check("patient A cannot load patient B's dashboard → 404", widgets(pt, p2.public_id)[0] == 404)
    check("unassigned nurse → 404 for patient A", widgets(nt2, p1.public_id)[0] == 404)
    s, nw = widgets(nt, p1.public_id)
    _, pw_ = widgets(pt)
    check("assigned nurse sees the same stamps as the patient", s == 200 and nw["self-care-stamps"] == pw_["self-care-stamps"]
          and nw["daily-report"] == pw_["daily-report"])
    check("patient B cannot report for patient A", report(p2t, pid=p1.public_id)[0] in (403, 404))

    # ================================================================ 放射線治療進度
    check("no radiotherapy appointments → radiation-progress null", w2["radiation-progress"] is None and pw_["radiation-progress"] is None)
    base = datetime(2026, 3, 1, 1, 0)  # 3/1 09:00 local
    rows = [  # (days from today, status, type, deleted)
        (-3, "completed", "radiotherapy", False), (-2, "completed", "radiotherapy", False), (-1, "cancelled", "radiotherapy", False),
        (-1, "rescheduled", "radiotherapy", False), (0, "checked_in", "radiotherapy", False), (1, "scheduled", "radiotherapy", False),
        (2, "scheduled", "radiotherapy", False), (1, "scheduled", "clinic_visit", False), (3, "scheduled", "radiotherapy", True),
    ]
    for n, (days, status, kind, deleted) in enumerate(rows):
        db.session.add(Appointment(patient_id=p1.id, appointment_type=kind, title=f"放射治療 {n}", scheduled_at=base + timedelta(days=days),
                                   location="放射腫瘤科（Demo）", status=status, created_by=nurse.id,
                                   deleted_at=utcnow() if deleted else None))
    db.session.commit()
    _, w = widgets(pt)
    rp = w["radiation-progress"]
    check("sessions = radiotherapy, not cancelled / rescheduled / deleted; done = completed",
          rp and rp["total_sessions"] == 5 and rp["completed_sessions"] == 2, rp)
    check("today's session (3rd) and the next one (4th, tomorrow)", rp["today_session"]["number"] == 3
          and rp["today_session"]["status"] == "checked_in" and rp["next_session"]["number"] == 4
          and rp["next_session"]["scheduled_at"] == "2026-03-02T01:00:00.000Z", rp)
    check("range in local dates", rp["first_date"] == "2026-02-26" and rp["last_date"] == "2026-03-03", rp)
    check("patient B does not see patient A's sessions", widgets(p2t)[1]["radiation-progress"] is None)
    check("chemo treatment-progress is still in the payload (other treatment info)", "treatment-progress" in w)
    pin_now(None)

    # ================================================================ nurse 處理項目: internal note, normal resolve flow
    r = c.post("/api/v1/symptoms/records", headers=H(pt), json={"patient_id": "me", "form_code": "daily_chemo_check", "values": [
        {"definition_code": "pain", "value_numeric": 1}, {"definition_code": "nausea", "value_numeric": 0},
        {"definition_code": "fatigue", "value_numeric": 2}, {"definition_code": "fever", "value_boolean": True}]})
    alert = db.session.query(Notification).filter_by(recipient_id=nurse.id, type="risk_alert", status="new").order_by(Notification.id.desc()).first()
    patient_copy = db.session.query(Notification).filter_by(recipient_id=p1.user_id, event_key=alert.event_key).one()
    note = "已協助掛號，請明日至門診報到。已電話聯繫病人。"
    for action, body in (("acknowledge", None), ("start", None), ("resolve", {"resolution_note": note})):
        rr = c.post(f"/api/v1/notifications/{alert.id}/{action}", json=body, headers={"Authorization": f"Bearer {nt}"})
    d = rr.get_json()["data"]
    check("nurse resolves with 處理項目 text → resolved, note stored", rr.status_code == 200 and d["status"] == "resolved"
          and d["handling"]["resolution_note"] == note, d.get("status"))
    pd = c.get(f"/api/v1/notifications/{patient_copy.id}", headers={"Authorization": f"Bearer {pt}"}).get_json()["data"]
    check("patient sees the status only, never the note", pd["status"] == "resolved" and "resolution_note" not in pd["handling"]
          and note not in str(pd), pd.get("handling"))
    check("unassigned nurse cannot resolve → 404", c.post(f"/api/v1/notifications/{alert.id}/resolve", json={"resolution_note": "x"},
                                                         headers={"Authorization": f"Bearer {nt2}"}).status_code == 404)

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
