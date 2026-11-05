"""病人與護理師聊天室 (Phase 1, text only): sending and reading, the daily auto reply (once per
patient per Asia/Taipei day — also when two messages race), Idempotency-Key, unread / read pointers,
history paging and persistence, patient / nurse isolation, ending an assignment, admin has no
access, audit (no message bodies), flood guard, and the Alembic migration (empty database and an
existing database, downgrade / upgrade)."""
import sys
from pathlib import Path
import json
import tempfile
import warnings
from datetime import date, datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import AuditLog, ChatMessage, ChatReadState, NursePatientAssignment, PatientProfile, User
from app.modules.chat import services as chat_services
from app.seeds.dev import seed_dev_data

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()
PASSWORD = "Train1234"
AUTO_REPLY = app.config["CHAT_SERVICE"]["auto_reply_text"]


def token(email, password="Demo@1234"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password}).get_json()["data"]["access_token"]


def H(t, key=None):
    h = {"Authorization": f"Bearer {t}"}
    if key:
        h["Idempotency-Key"] = key
    return h


def err(r):
    j = r.get_json() or {}
    return r.status_code, j.get("error", {}).get("code"), [d.get("field") for d in j.get("error", {}).get("details", [])]


_keys = iter(range(1, 10_000))


def send(t, pid, text, key=None):
    return c.post(f"/api/v1/patients/{pid}/messages", json={"body": text}, headers=H(t, key or f"k-{next(_keys)}"))


def msgs(t, pid, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return c.get(f"/api/v1/patients/{pid}/messages" + (f"?{q}" if q else ""), headers=H(t))


real_utcnow = chat_services.utcnow


def at_utc(*args):
    """Freeze the chat clock (naive UTC)."""
    chat_services.utcnow = lambda: datetime(*args)


def auto_replies(patient_id, day=None):
    q = select(ChatMessage).filter_by(patient_id=patient_id, kind="auto_reply")
    if day:
        q = q.filter_by(auto_reply_date=day)
    return db.session.execute(q).scalars().all()


with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()
    at, nt, pt = token("admin01@demo.local"), token("nurse01@demo.local"), token("patient01@demo.local")
    nurse = db.session.query(User).filter_by(email="nurse01@demo.local").one()
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    P1 = p1.public_id

    # a second pair (patient B with a login + nurse B assigned only to B) through the training feature
    r = c.post("/api/v1/admin/training-accounts", json={
        "start": 1, "count": 1, "patient_prefix": "chatpt", "nurse_prefix": "chatnu", "domain": "demo.local",
        "patient_name_prefix": "聊天病人", "nurse_name_prefix": "聊天護理師", "password": PASSWORD, "confirm": True}, headers=H(at))
    check("setup: training pair (patient B + nurse B) created", r.status_code in (200, 201), r.get_json())
    ptB, ntB = token("chatpt001@demo.local", PASSWORD), token("chatnu001@demo.local", PASSWORD)
    pB = db.session.query(PatientProfile).join(User, User.id == PatientProfile.user_id).filter(User.email == "chatpt001@demo.local").one()
    PB = pB.public_id

    # ================================================================ empty conversation + service info
    r = msgs(pt, "me")
    j = r.get_json()
    svc = j["meta"]["service"]
    check("patient: empty conversation via me", r.status_code == 200 and j["data"] == [] and j["meta"]["has_more"] is False
          and j["meta"]["unread_count"] == 0, j)
    check("service info: 義大醫院放射腫瘤科, hours, 07-6150011 分機 2205 (not the demo institution)",
          svc["name"] == "義大醫院放射腫瘤科" and svc["phone_text"] == "07-6150011 分機 2205"
          and "09:00-12:30" in svc["hours_text"] and "13:30-17:30" in svc["hours_text"] and "Demo" not in json.dumps(svc, ensure_ascii=False), svc)
    check("auto reply text is the requested 義大醫院 text",
          AUTO_REPLY.startswith("您好！這裡是義大醫院放射腫瘤科\n小幫手將於上班時間星期一至五，早上 09:00-12:30，以及下午 13:30-17:30")
          and AUTO_REPLY.endswith("至放射腫瘤科門診，由醫師看診評估，謝謝您") and "07-6150011 分機 2205" in AUTO_REPLY)

    # service hours (Asia/Taipei, Monday–Friday 09:00–12:30 / 13:30–17:30)
    hours = [(datetime(2026, 11, 2, 0, 59), False), (datetime(2026, 11, 2, 1, 0), True), (datetime(2026, 11, 2, 4, 29), True),
             (datetime(2026, 11, 2, 4, 30), False), (datetime(2026, 11, 2, 5, 30), True), (datetime(2026, 11, 2, 9, 30), False),
             (datetime(2026, 11, 7, 2, 0), False)]  # Mon 08:59 / 09:00 / 12:29 / 12:30 / 13:30 / 17:30, Sat 10:00
    check("in_service_hours: boundaries and weekend in Asia/Taipei",
          [chat_services.in_service_hours(t) for t, _ in hours] == [want for _, want in hours])

    # ================================================================ validation / idempotency key
    check("POST without Idempotency-Key → 428", c.post("/api/v1/patients/me/messages", json={"body": "hi"}, headers=H(pt)).status_code == 428)
    bad = [err(c.post("/api/v1/patients/me/messages", json=b, headers=H(pt, f"bad-{i}")))
           for i, b in enumerate([{}, {"body": ""}, {"body": "   \n "}, {"body": 5}, {"body": "字" * 1001}])]
    check("body required, text, 1–1000 characters → 400 body", all(b[0] == 400 and b[2] == ["body"] for b in bad), bad)
    check("non-object JSON → 400", c.post("/api/v1/patients/me/messages", json=["x"], headers=H(pt, "bad-arr")).status_code == 400)
    check("1000 characters is accepted (after trimming)", send(pt, "me", "  " + "字" * 1000 + "  ").status_code == 201)
    db.session.query(ChatMessage).delete()
    db.session.query(ChatReadState).delete()
    db.session.commit()

    # ================================================================ patient sends: first of the day → one auto reply
    at_utc(2026, 11, 2, 1, 5)  # Mon 09:05 Asia/Taipei
    r = send(pt, "me", "  我今天皮膚有點紅，需要擦藥嗎？ ", key="first")
    d = r.get_json()["data"]
    m1, a1 = d["message"], d["auto_reply"]
    check("patient → 201, own text message (trimmed, is_mine)", r.status_code == 201 and m1["sender_role"] == "patient" and m1["kind"] == "text"
          and m1["body"] == "我今天皮膚有點紅，需要擦藥嗎？" and m1["is_mine"] is True and m1["created_at"] == "2026-11-02T01:05:00.000Z", m1)
    check("first message of the day → system auto reply after it (not a nurse reply)",
          a1 and a1["kind"] == "auto_reply" and a1["sender_role"] == "system" and a1["sender_label"] == "系統自動回覆"
          and a1["body"] == AUTO_REPLY and a1["id"] > m1["id"] and a1["is_mine"] is False, a1)
    r2 = send(pt, "me", "另外想問明天幾點治療？")
    check("second message the same day → no auto reply", r2.status_code == 201 and r2.get_json()["data"]["auto_reply"] is None)
    at_utc(2026, 11, 2, 15, 59)  # Mon 23:59 Asia/Taipei
    check("23:59 Asia/Taipei the same day → still no auto reply", send(pt, "me", "晚上補充").get_json()["data"]["auto_reply"] is None)
    check("exactly one auto reply stored for 2026-11-02", len(auto_replies(p1.id, date(2026, 11, 2))) == 1)

    # reopening / reloading never creates one
    before = db.session.query(ChatMessage).count()
    for _ in range(3):
        msgs(pt, "me")
    check("reading the conversation (reload / reopen) creates nothing", db.session.query(ChatMessage).count() == before)

    # idempotency: the same key returns the stored message
    r = send(pt, "me", "  我今天皮膚有點紅，需要擦藥嗎？ ", key="first")
    check("same Idempotency-Key + body → replay of the same message, nothing new",
          r.status_code == 201 and r.headers.get("Idempotent-Replayed") == "true" and r.get_json()["data"]["message"]["id"] == m1["id"]
          and db.session.query(ChatMessage).count() == before)
    check("same key, different body → 422 IDEMPOTENCY_KEY_MISMATCH", err(send(pt, "me", "別的內容", key="first"))[:2] == (422, "IDEMPOTENCY_KEY_MISMATCH"))

    # next Asia/Taipei day starts at 16:00 UTC (the UTC date is still 11-02)
    at_utc(2026, 11, 2, 16, 0)
    a2 = send(pt, "me", "早安，今天要治療").get_json()["data"]["auto_reply"]
    check("00:00 Asia/Taipei (16:00 UTC, same UTC date) → new day → auto reply again",
          a2 is not None and [m.auto_reply_date for m in auto_replies(p1.id)] == [date(2026, 11, 2), date(2026, 11, 3)])

    # race: both requests saw "no auto reply yet" → the unique (patient_id, auto_reply_date) keeps one
    real_exists = chat_services._auto_reply_exists
    chat_services._auto_reply_exists = lambda *_: False
    r = send(pt, "me", "同時送出的第二則")
    chat_services._auto_reply_exists = real_exists
    check("concurrent first messages: message stored, second auto reply rejected by the database",
          r.status_code == 201 and r.get_json()["data"]["auto_reply"] is None and len(auto_replies(p1.id, date(2026, 11, 3))) == 1)
    try:
        with db.session.begin_nested():
            db.session.add(ChatMessage(patient_id=p1.id, sender_role="system", kind="auto_reply", body="x", auto_reply_date=date(2026, 11, 3)))
        dup = False
    except IntegrityError:
        dup = True
    check("database: a second auto reply for the same patient and day → IntegrityError", dup)
    try:
        with db.session.begin_nested():
            db.session.add(ChatMessage(patient_id=p1.id, sender_id=nurse.id, sender_role="system", kind="text", body="x"))
        bad_kind = False
    except IntegrityError:
        bad_kind = True
    check("database: a text message cannot claim to be from the system (check constraint)", bad_kind)
    db.session.rollback()
    patient_msgs = db.session.query(ChatMessage).filter_by(patient_id=p1.id, sender_role="patient").count()

    # ================================================================ nurse reads and replies
    r = msgs(nt, P1)
    j = r.get_json()
    labels = {m["sender_role"]: m["sender_label"] for m in j["data"]}
    check("assigned nurse reads the conversation (oldest first)", r.status_code == 200 and [m["id"] for m in j["data"]] == sorted(m["id"] for m in j["data"]))
    check("nurse sees the patient's name and the auto reply marked as system",
          labels.get("patient") == p1.display_name and labels.get("system") == "系統自動回覆")
    check("nurse unread = the patient's messages", j["meta"]["unread_count"] == patient_msgs, (j["meta"]["unread_count"], patient_msgs))
    check("nurse /chat/unread-count", c.get("/api/v1/chat/unread-count", headers=H(nt)).get_json()["data"] == {"unread_count": patient_msgs, "patients_with_unread": 1})
    inbox = c.get("/api/v1/chat/inbox", headers=H(nt)).get_json()
    row = next(x for x in inbox["data"] if x["patient"]["id"] == P1)
    check("inbox: assigned patient with unread count and last message preview", row["unread_count"] == patient_msgs
          and row["last_message"]["sender_role"] == "patient" and row["patient"]["patient_code"] == "P00001" and inbox["meta"]["unread_count"] == patient_msgs, row)
    check("inbox: only assigned patients (not patient B)", PB not in [x["patient"]["id"] for x in inbox["data"]])

    at_utc(2026, 11, 3, 2, 0)
    r = send(nt, P1, "您好，可以先擦醫師開的藥膏，明天治療時再讓我們看看。")
    nm = r.get_json()["data"]
    check("nurse reply → 201, sender_role nurse, no auto reply", r.status_code == 201 and nm["message"]["sender_role"] == "nurse"
          and nm["auto_reply"] is None and nm["message"]["sender_label"] == nurse.display_name)
    check("replying moves the nurse's read pointer: nurse unread 0", c.get("/api/v1/chat/unread-count", headers=H(nt)).get_json()["data"]["unread_count"] == 0)

    # ================================================================ patient receives
    check("patient unread-count = 1 nurse message", c.get("/api/v1/chat/unread-count", headers=H(pt)).get_json()["data"] == {"unread_count": 1})
    j = msgs(pt, "me").get_json()
    nurse_row = next(m for m in j["data"] if m["sender_role"] == "nurse")
    check("patient sees the reply as 放射腫瘤科護理師 (never the nurse's name)",
          nurse_row["sender_label"] == "放射腫瘤科護理師" and nurse.display_name not in json.dumps(j, ensure_ascii=False) and nurse_row["is_mine"] is False)
    check("patient: the system auto reply is distinguishable from the nurse", {m["sender_role"] for m in j["data"]} == {"patient", "system", "nurse"}
          and all(m["sender_label"] == "系統自動回覆" for m in j["data"] if m["kind"] == "auto_reply"))
    last_id = j["data"][-1]["id"]
    r = c.post("/api/v1/patients/me/messages/read", json={"last_message_id": last_id}, headers=H(pt))
    check("mark read → unread 0", r.status_code == 200 and r.get_json()["data"] == {"last_read_message_id": last_id, "unread_count": 0})
    r = c.post("/api/v1/patients/me/messages/read", json={"last_message_id": 1}, headers=H(pt))
    check("read pointer never moves back", r.get_json()["data"]["last_read_message_id"] == last_id)
    r = c.post("/api/v1/patients/me/messages/read", json={"last_message_id": 10**9}, headers=H(pt))
    check("read pointer is capped at the newest message", r.get_json()["data"]["last_read_message_id"] == last_id)
    bad = [err(c.post("/api/v1/patients/me/messages/read", json=b, headers=H(pt)))[0] for b in ({}, {"last_message_id": "3"}, {"last_message_id": -1}, {"last_message_id": True})]
    check("read: last_message_id must be a non-negative integer", bad == [400] * 4, bad)

    # ================================================================ history paging + persistence
    total = db.session.query(ChatMessage).filter_by(patient_id=p1.id).count()
    page1 = msgs(pt, "me", limit=3).get_json()
    page2 = msgs(pt, "me", limit=3, before_id=page1["data"][0]["id"]).get_json()
    check("history: newest page + has_more, older page before it", len(page1["data"]) == 3 and page1["meta"]["has_more"] is True
          and page2["data"][-1]["id"] < page1["data"][0]["id"])
    allm = msgs(pt, "me", limit=100).get_json()
    check("history: all messages, oldest first, has_more false", len(allm["data"]) == total and allm["meta"]["has_more"] is False)
    newer = msgs(pt, "me", after_id=page1["data"][0]["id"]).get_json()["data"]
    check("polling after_id → only newer messages", [m["id"] for m in newer] == [m["id"] for m in page1["data"][1:]])
    check("after_id + before_id → 400; limit 0 / 101 → 400",
          [msgs(pt, "me", after_id=1, before_id=5).status_code, msgs(pt, "me", limit=0).status_code, msgs(pt, "me", limit=101).status_code] == [400] * 3)
    db.session.expire_all()
    pt_again = token("patient01@demo.local")
    check("persistence: a new sign-in sees the same history", [m["id"] for m in msgs(pt_again, "me", limit=100).get_json()["data"]] == [m["id"] for m in allm["data"]])

    # ================================================================ isolation
    codes = [msgs(pt, PB).status_code, send(pt, PB, "偷看").status_code,
             c.post(f"/api/v1/patients/{PB}/messages/read", json={"last_message_id": 0}, headers=H(pt)).status_code]
    check("patient A → patient B's conversation: read / send / mark read → 404", codes == [404] * 3, codes)
    check("patient B sees only their own (empty) conversation", msgs(ptB, "me").get_json()["data"] == [] and msgs(ptB, P1).status_code == 404)
    check("patient B: random / malformed patient id → 404", msgs(ptB, "00000000-0000-0000-0000-000000000000").status_code == 404 and msgs(ptB, "1").status_code == 404)
    codes = [msgs(ntB, P1).status_code, send(ntB, P1, "x").status_code,
             c.post(f"/api/v1/patients/{P1}/messages/read", json={"last_message_id": 0}, headers=H(ntB)).status_code, msgs(ntB, "me").status_code]
    check("nurse B (not assigned to patient A): read / send / mark read / me → 404", codes == [404] * 4, codes)
    check("nurse B inbox: only patient B", [x["patient"]["id"] for x in c.get("/api/v1/chat/inbox", headers=H(ntB)).get_json()["data"]] == [PB])
    other = User(role=nurse.role, email="n9@demo.local", password_hash=nurse.password_hash, display_name="其他護理師", password_changed_at=nurse.password_changed_at)
    db.session.add(other)
    db.session.commit()
    ot = token("n9@demo.local")
    codes = [msgs(ot, P1).status_code, send(ot, P1, "x").status_code, msgs(ot, PB).status_code]
    check("unassigned nurse: 404 everywhere, empty inbox, unread 0", codes == [404] * 3
          and c.get("/api/v1/chat/inbox", headers=H(ot)).get_json()["data"] == []
          and c.get("/api/v1/chat/unread-count", headers=H(ot)).get_json()["data"]["unread_count"] == 0, codes)

    # admin: no chat access in this version; patients have no inbox
    codes = [msgs(at, P1).status_code, send(at, P1, "x").status_code,
             c.post(f"/api/v1/patients/{P1}/messages/read", json={"last_message_id": 0}, headers=H(at)).status_code,
             c.get("/api/v1/chat/inbox", headers=H(at)).status_code, c.get("/api/v1/chat/unread-count", headers=H(at)).status_code]
    check("admin: read / send / read pointer / inbox / unread → 403", codes == [403] * 5, codes)
    check("patient: nurse inbox → 403", c.get("/api/v1/chat/inbox", headers=H(pt)).status_code == 403)
    check("no token → 401", c.get("/api/v1/patients/me/messages").status_code == 401 and c.get("/api/v1/chat/unread-count").status_code == 401)

    # patient B: the first message of their own day gets its own auto reply (per patient)
    at_utc(2026, 11, 3, 3, 0)
    check("auto reply is per patient (patient B's first message today gets one)", send(ptB, "me", "你好").get_json()["data"]["auto_reply"] is not None)

    # ================================================================ audit (never the message text)
    views_before = db.session.query(AuditLog).filter_by(resource_type="chat_messages", action="VIEW").count()
    msgs(nt, P1)
    msgs(nt, P1, after_id=last_id)
    views_after = db.session.query(AuditLog).filter_by(resource_type="chat_messages", action="VIEW").count()
    check("nurse opening the conversation → one VIEW audit; polling (after_id) adds none", views_after == views_before + 1)
    creates = db.session.query(AuditLog).filter_by(resource_type="chat_messages", action="CREATE").all()
    check("every message is audited (CREATE) without its text", len(creates) >= patient_msgs + 1
          and all("body" not in (a.changes or {}) and "皮膚" not in json.dumps(a.changes, ensure_ascii=False) for a in creates))
    check("patients reading their own conversation are not logged as VIEW",
          db.session.query(AuditLog).filter_by(resource_type="chat_messages", action="VIEW", actor_role="patient").count() == 0)

    # ================================================================ flood guard
    at_utc(2026, 11, 3, 4, 0)
    codes = [send(ptB, "me", f"第 {i} 則").status_code for i in range(20)]
    check("flood guard: 20 messages per minute, the next one → 429 RATE_LIMITED",
          codes == [201] * 20 and err(send(ptB, "me", "太多了"))[:2] == (429, "RATE_LIMITED"), codes)  # the 03:00 message is outside the window
    at_utc(2026, 11, 3, 4, 2)
    check("…and accepted again after the window", send(ptB, "me", "過一會").status_code == 201)

    # ================================================================ ending the assignment ends chat access at once
    asg = db.session.execute(select(NursePatientAssignment).filter_by(nurse_id=nurse.id, patient_id=p1.id, ended_at=None)).scalar_one()
    r = c.post(f"/api/v1/patients/{P1}/nurse-assignments/{asg.id}/end", json={}, headers=H(at))
    codes = [msgs(nt, P1).status_code, send(nt, P1, "還在嗎").status_code,
             c.post(f"/api/v1/patients/{P1}/messages/read", json={"last_message_id": 0}, headers=H(nt)).status_code]
    check("assignment ended → the nurse gets 404 immediately (read / send / mark read)", r.status_code == 200 and codes == [404] * 3, (r.status_code, codes))
    check("…and the patient is gone from the nurse's inbox and unread count",
          P1 not in [x["patient"]["id"] for x in c.get("/api/v1/chat/inbox", headers=H(nt)).get_json()["data"]]
          and c.get("/api/v1/chat/unread-count", headers=H(nt)).get_json()["data"]["unread_count"] == 0)
    check("the patient keeps the full history", len(msgs(pt, "me", limit=100).get_json()["data"]) == total)
    chat_services.utcnow = real_utcnow

# ================================================================ migration: empty database and an existing one
from flask_migrate import downgrade, upgrade

migrations = str(Path(__file__).resolve().parents[1] / "migrations")
for label, seeded in (("empty database", False), ("existing database (previous head + data)", True)):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        from app.config import DevelopmentConfig

        # a separate app on a throw-away SQLite file — never the local development database. The
        # engine URL comes from the config class (DATABASE_URL is read at import), so it is set there.
        target = f"sqlite:///{Path(tmp, 'mig.db').as_posix()}"
        saved = (DevelopmentConfig.SQLALCHEMY_DATABASE_URI, DevelopmentConfig.EMAIL_CAPTURE_DIR)
        DevelopmentConfig.SQLALCHEMY_DATABASE_URI = target
        DevelopmentConfig.EMAIL_CAPTURE_DIR = None
        mapp = create_app("development")
        DevelopmentConfig.SQLALCHEMY_DATABASE_URI, DevelopmentConfig.EMAIL_CAPTURE_DIR = saved
        with mapp.app_context(), warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # migrations/env.py uses Flask-SQLAlchemy's get_engine()
            if str(db.engine.url) != target:
                raise SystemExit(f"refusing to migrate {db.engine.url}: not the throw-away database")
            if seeded:
                upgrade(directory=migrations, revision="f1a7c3d2b9e4")
                seed_dev_data()
                db.session.commit()
                users_before = db.session.query(User).count()
            upgrade(directory=migrations)
            insp = db.inspect(db.engine)
            has = {"chat_messages", "chat_read_states"} <= set(insp.get_table_names())
            uniques = {u["name"] for u in insp.get_unique_constraints("chat_messages")}
            check(f"migration ({label}): upgrade creates chat_messages + chat_read_states with the daily auto-reply unique key",
                  has and "uq_chat_messages_patient_id_auto_reply_date" in uniques, uniques)
            if seeded:
                check(f"migration ({label}): existing rows untouched", db.session.query(User).count() == users_before)
                downgrade(directory=migrations, revision="f1a7c3d2b9e4")
                gone = not ({"chat_messages", "chat_read_states"} & set(db.inspect(db.engine).get_table_names()))
                upgrade(directory=migrations)
                check(f"migration ({label}): downgrade drops only the chat tables, upgrade again works",
                      gone and db.session.query(User).count() == users_before and "chat_messages" in db.inspect(db.engine).get_table_names())
            db.session.remove()
            db.engine.dispose()  # Windows: close the SQLite file before the temp directory is removed

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
