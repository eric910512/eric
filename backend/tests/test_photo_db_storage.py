"""皮膚照片 database storage (PHOTO_STORAGE_BACKEND=database, images as BYTEA in ``symptom_photo_blobs``): the
switch (quota from the environment, unsafe settings off, staging needs PostgreSQL), upload → two image rows in
the photo's transaction, reads through the image endpoint only, list / history queries that never touch the
image table, database write and commit failures (full rollback), removal and clean-up in one transaction
(delete failure → nothing changes), duplicates and Idempotency-Key replays, the quota (exact limit, 507
PHOTO_STORAGE_FULL, other features unaffected, warnings), `flask photos verify` / `storage-report`, and access
— on a throw-away SQLite database with synthetic images. Concurrency, BYTEA / TOAST, the 200 MiB quota and
backup / restore run on a real PostgreSQL: tests/predeploy/photo_db_storage_check.py."""
import io
import logging
import os
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ["PHOTO_DB_QUOTA_BYTES"] = "209715200"  # 200 MiB, from the environment like a deployment (read by app.config)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image
from sqlalchemy import event, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import (IdempotencyRecord, NursePatientAssignment, PatientProfile, Role, SymptomPhoto, SymptomPhotoBlob,
                        User)
from app.models.base import utcnow
from app.modules.photo import services as photo_services
from app.seeds.dev import seed_dev_data
from app.services.photo_storage import DatabasePhotoStorage, PhotoStorageError, init_photos

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


log_lines = []


class _Collect(logging.Handler):
    def emit(self, record):
        log_lines.append(record.getMessage())


logging.getLogger().addHandler(_Collect())
logging.getLogger().setLevel(logging.INFO)

app = create_app("testing")
app.config.update(PHOTO_STORAGE_BACKEND="database", PHOTO_MAX_PER_10_MINUTES=1000, PHOTO_MAX_PER_DAY=1000)
init_photos(app)
c = app.test_client()
feature = app.extensions["photo_feature"]
storage = feature.storage
PII = ["patient01@demo.local", "p2@demo.local", "P00001", "P09002"]


def H(t, key=None):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": key or str(uuid.uuid4())}


def code(r):
    return (r.get_json(silent=True) or {}).get("error", {}).get("code")


def jpeg(gps=False):
    buf = io.BytesIO()
    kw = {}
    if gps:
        ex = Image.Exif()
        ex[0x010F], ex[0x8825] = "TestMaker", {1: "N", 2: (25.0, 2.0, 0.0), 3: "E", 4: (121.0, 33.0, 0.0)}
        kw["exif"] = ex
    Image.frombytes("RGB", (60, 45), os.urandom(60 * 45 * 3)).resize((2000, 1500)).save(buf, "JPEG", quality=85, **kw)
    return buf.getvalue()


def upload(t, data=None, key=None):
    r = c.post("/api/v1/patients/me/symptom-photos", data={"file": (io.BytesIO(data or jpeg()), "IMG_0001.jpg", "image/jpeg")},
               headers=H(t, key), content_type="multipart/form-data")
    return r, (r.get_json(silent=True) or {}).get("data", {})


def content(t, photo_id, variant="full"):
    return c.get(f"/api/v1/symptom-photos/{photo_id}/content?variant={variant}", headers={"Authorization": f"Bearer {t}"} if t else {})


def report(t, photos=None):
    body = {"patient_id": "me", "form_code": "rt_skin_self_assessment", "values": [{"definition_code": "skin_itch", "option_code": "yes"}]}
    if photos is not None:
        body["photos"] = [{"id": p} for p in photos]
    return c.post("/api/v1/symptoms/records", json=body, headers=H(t))


def counts():
    with app.app_context():
        return db.session.query(SymptomPhoto).count(), db.session.query(SymptomPhotoBlob).count()


def blobs_of(public_id):
    with app.app_context():
        p = db.session.query(SymptomPhoto).filter_by(public_id=public_id).one()
        return {b.variant: (b.byte_size, b.sha256, bytes(b.data)) for b in db.session.query(SymptomPhotoBlob).filter_by(photo_id=p.id)}, p


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
    pid = db.session.query(PatientProfile).filter_by(patient_code="P00001").one().public_id
tok = {e: c.post("/api/v1/auth/login", json={"email": e, "password": "Demo@1234"}).get_json()["data"]["access_token"]
       for e in ("patient01@demo.local", "p2@demo.local", "nurse01@demo.local", "nurse02@demo.local", "admin01@demo.local")}
pt, p2t, nt, nt2, at = (tok[e] for e in ("patient01@demo.local", "p2@demo.local", "nurse01@demo.local", "nurse02@demo.local", "admin01@demo.local"))
version = c.get("/api/v1/symptom-photos/settings", headers=H(pt)).get_json()["data"]["consent"]["version"]
c.post("/api/v1/patients/me/photo-consent", json={"version": version}, headers=H(pt))

# ================================================================ switch
check("quota from the environment: PHOTO_DB_QUOTA_BYTES=209715200 (200 MiB) → app.config and the storage",
      app.config["PHOTO_DB_QUOTA_BYTES"] == 209715200 and storage.quota_bytes == 209715200)
check("database backend on: DatabasePhotoStorage, transactional, uploads on", isinstance(storage, DatabasePhotoStorage)
      and storage.transactional and feature.upload_enabled, feature.reason)


def status_for(config_name="testing", **over):
    cfg = {"CONFIG_NAME": config_name, "PHOTO_UPLOADS_ENABLED": True, "PHOTO_STORAGE_BACKEND": "database",
           "PHOTO_DB_QUOTA_BYTES": 209715200, "PHOTO_CONSENT_APPROVED_VERSION": version,
           "SQLALCHEMY_DATABASE_URI": "postgresql://u:p@127.0.0.1:1/x", **over}
    fake = SimpleNamespace(config=cfg, testing=False, instance_path=".", extensions={})
    init_photos(fake)
    return fake.extensions["photo_feature"]


for label, over in {"quota not set": dict(PHOTO_DB_QUOTA_BYTES=None), "quota 0": dict(PHOTO_DB_QUOTA_BYTES=0),
                    "quota negative": dict(PHOTO_DB_QUOTA_BYTES=-1), "quota as text": dict(PHOTO_DB_QUOTA_BYTES="209715200"),
                    "quota True": dict(PHOTO_DB_QUOTA_BYTES=True), "warn ≥ critical": dict(PHOTO_DB_WARN_PERCENT=90, PHOTO_DB_CRITICAL_PERCENT=80),
                    "critical > 100": dict(PHOTO_DB_CRITICAL_PERCENT=120), "disk size 0": dict(PHOTO_DB_DISK_BYTES=0)}.items():
    f = status_for(**over)
    check(f"{label} → photos off (photo_quota_not_configured), no storage", not f.upload_enabled and f.storage is None
          and f.reason == "photo_quota_not_configured", f.reason)
f = status_for("staging", SQLALCHEMY_DATABASE_URI="sqlite:///x.db")
check("staging + database backend on SQLite → refused (database_storage_requires_postgresql)", not f.upload_enabled
      and f.reason == "database_storage_requires_postgresql")
f = status_for("staging")
check("staging + PostgreSQL + quota + approved consent → on (no AWS setting needed)", f.upload_enabled and isinstance(f.storage, DatabasePhotoStorage))
f = status_for("production", PHOTO_UPLOADS_ENABLED=False)
check("PHOTO_UPLOADS_ENABLED false → off even with database storage (disabled_by_config); photos stay readable",
      not f.upload_enabled and f.view_enabled and f.reason == "disabled_by_config")

# ================================================================ upload → rows in one transaction
src = jpeg(gps=True)
r, a = upload(pt, src)
A = a.get("id")
blobs, photo_a = blobs_of(A)
check("upload → 201 pending; the photo row records storage_backend=database", r.status_code == 201 and photo_a.storage_backend == "database")
check("…two image rows (full + thumb) referencing the photo; sizes = the photo's file_size / thumb_file_size",
      set(blobs) == {"full", "thumb"} and blobs["full"][0] == photo_a.file_size and blobs["thumb"][0] == photo_a.thumb_file_size)
full = blobs["full"][2]
im = Image.open(io.BytesIO(full))
check("…the stored bytes are the re-encoded JPEG (sha256 = checksum), ≤ 1600 px, no EXIF / GPS",
      blobs["full"][1] == photo_a.checksum_sha256 and max(im.size) <= 1600 and len(im.getexif()) == 0 and b"TestMaker" not in full and full != src)
rf, rt_ = content(pt, A), content(pt, A, "thumb")
check("image endpoint: full + thumb bytes = the stored rows; no-store, nosniff", rf.status_code == 200 and rf.data == full
      and rt_.data == blobs["thumb"][2] and "no-store" in rf.headers["Cache-Control"] and rf.headers["X-Content-Type-Options"] == "nosniff")
check("no image file on any disk: the storage has no directory", not hasattr(storage, "root"))

# ================================================================ list / history never load BYTEA
check("attach to a report → 201", report(pt, [A]).status_code == 201)
statements = []
with app.app_context():
    listener = lambda conn, cursor, statement, params, context, many: statements.append(statement)  # noqa: E731
    event.listen(db.engine, "before_cursor_execute", listener)
    try:
        calls = {"nurse history": c.get(f"/api/v1/patients/{pid}/symptom-photos", headers=H(nt)),
                 "patient history": c.get("/api/v1/patients/me/symptom-photos", headers=H(pt)),
                 "patient records": c.get("/api/v1/symptoms/records/me?form_code=rt_skin_self_assessment", headers=H(pt)),
                 "nurse records": c.get(f"/api/v1/symptoms/records/{pid}", headers=H(nt)),
                 "review queue": c.get("/api/v1/dashboard/widgets/pending-symptom-reviews/data", headers=H(nt))}
        list_sql = list(statements)
        statements.clear()
        img = content(nt, A, "thumb")
        image_sql = list(statements)
    finally:
        event.remove(db.engine, "before_cursor_execute", listener)
check("list / history / record / review endpoints answer 200", all(r.status_code == 200 for r in calls.values()),
      {k: r.status_code for k, r in calls.items()})
check(f"…none of their {len(list_sql)} SQL statements touches symptom_photo_blobs (no BYTEA loaded)",
      not any("symptom_photo_blobs" in s for s in list_sql))
blob_sql = [s for s in image_sql if "symptom_photo_blobs" in s]
check("the image endpoint reads exactly one image: SELECT symptom_photo_blobs.data … WHERE storage_key",
      img.status_code == 200 and len(blob_sql) == 1 and "symptom_photo_blobs.data" in blob_sql[0] and "storage_key" in blob_sql[0], blob_sql)

# ================================================================ failures: nothing half done
real_put = DatabasePhotoStorage.put


def put_bad_thumb(self, key, data, content_type):  # a real database error: the CHECK byte_size > 0 rejects it
    return real_put(self, key, b"" if key.endswith("/thumb.jpg") else data, content_type)


DatabasePhotoStorage.put = put_bad_thumb
before = counts()
key = str(uuid.uuid4())
r, _ = upload(pt, key=key)
DatabasePhotoStorage.put = real_put
with app.app_context():
    rec = db.session.query(IdempotencyRecord).filter_by(idempotency_key=key).one()
check("database write fails on the thumbnail (CHECK violation) → 503 PHOTO_STORAGE_UNAVAILABLE",
      r.status_code == 503 and code(r) == "PHOTO_STORAGE_UNAVAILABLE", (r.status_code, code(r)))
check("…full rollback: no photo row, no image row (also not the full image written first); the key holds the 503",
      counts() == before and rec.resource_id is None and rec.response_status == 503)
check("…logged with a safe code only", any("photo storage write failed (IntegrityError)" in ln for ln in log_lines))

fail_once = {"armed": True}


def mark_images(session, flush_context, instances):  # image rows flushed in this transaction
    if any(isinstance(o, SymptomPhotoBlob) for o in session.new):
        session.info["images_flushed"] = True


def failing_commit(session):  # the upload's final commit (photo + images + Idempotency-Key)
    if fail_once["armed"] and session.info.pop("images_flushed", False):
        fail_once["armed"] = False
        raise RuntimeError("simulated commit failure")


hooks = [("before_flush", mark_images), ("before_commit", failing_commit)]
for name, fn in hooks:
    event.listen(Session, name, fn)  # every session (each request has its own)
before = counts()
key = str(uuid.uuid4())
same = jpeg()
r1, _ = upload(pt, same, key=key)
mid = counts()
r2, d2 = upload(pt, same, key=key)
for name, fn in hooks:
    event.remove(Session, name, fn)
check("commit fails after photo + images were written → 500, and nothing at all was stored",
      r1.status_code == 500 and mid == before and not fail_once["armed"], (r1.status_code, before, mid))
check("…retry with the same Idempotency-Key (allowed after an unexpected 500) → 201, exactly one photo + two images",
      r2.status_code == 201 and counts() == (before[0] + 1, before[1] + 2))
B = d2["id"]

real_delete = DatabasePhotoStorage.delete


def failing_delete(self, key):
    raise PhotoStorageError("database storage delete failed", "OperationalError")


DatabasePhotoStorage.delete = failing_delete
r = c.delete(f"/api/v1/symptom-photos/{B}", headers=H(pt))
DatabasePhotoStorage.delete = real_delete
blobs_b, photo_b = blobs_of(B)
check("removal: the image delete fails → 503, the photo stays pending WITH both images (one transaction)",
      r.status_code == 503 and code(r) == "PHOTO_STORAGE_UNAVAILABLE" and photo_b.status == "pending" and len(blobs_b) == 2)
r = c.delete(f"/api/v1/symptom-photos/{B}", headers=H(pt))
blobs_b, photo_b = blobs_of(B)
check("removal works again → 200: discarded and both image rows gone (same commit)", r.status_code == 200 and photo_b.status == "discarded" and not blobs_b)
check("an attached photo cannot be removed → 409 PHOTO_RETAINED, its images kept",
      c.delete(f"/api/v1/symptom-photos/{A}", headers=H(pt)).status_code == 409 and len(blobs_of(A)[0]) == 2)

_, d = upload(pt)
C = d["id"]
with app.app_context():
    p = db.session.query(SymptomPhoto).filter_by(public_id=C).one()
    p.created_at = utcnow() - timedelta(hours=25)
    db.session.commit()
    DatabasePhotoStorage.delete = failing_delete
    n_fail = photo_services.purge_expired_pending()
    DatabasePhotoStorage.delete = real_delete
st_fail = blobs_of(C)
with app.app_context():
    n_ok = photo_services.purge_expired_pending()
st_ok = blobs_of(C)
check("clean-up while image deletes fail → nothing changes (0 discarded, still pending with images)",
      n_fail == 0 and st_fail[1].status == "pending" and len(st_fail[0]) == 2)
check("clean-up → the expired pending photo discarded and its images deleted in the same commit",
      n_ok == 1 and st_ok[1].status == "discarded" and st_ok[1].delete_reason == "expired_unattached" and not st_ok[0])

with app.app_context():
    try:
        db.session.query(SymptomPhoto).filter_by(public_id=A).delete()
        db.session.flush()
        restricted = False
    except IntegrityError:
        restricted = True
    db.session.rollback()
check("foreign key RESTRICT: a photo row with images cannot be deleted", restricted)

# ================================================================ duplicates and replays
same, key = jpeg(), str(uuid.uuid4())
r1, d1 = upload(pt, same, key=key)
before = counts()
r2, d2 = upload(pt, same, key=key)
r3, d3 = upload(pt, same)
check("same Idempotency-Key again → the same photo replayed, nothing stored again", r1.status_code == 201 and r2.status_code == 201
      and d2["id"] == d1["id"] and counts() == before)
check("same picture with a NEW key while still pending → 200 with that photo, no second copy", r3.status_code == 200
      and d3["id"] == d1["id"] and counts() == before)

# ================================================================ quota
with app.app_context():
    used = storage.used_bytes()
    nxt = jpeg()
    image = photo_services.prepare_upload(nxt, "image/jpeg")
    need = len(image.full) + len(image.thumb)
saved_quota = storage.quota_bytes
storage.quota_bytes = used + need - 1
before = counts()
key = str(uuid.uuid4())
r, _ = upload(pt, nxt, key=key)
full_body = r.get_data(as_text=True)
check("quota = used + needed − 1 byte → 507 PHOTO_STORAGE_FULL, nothing stored", r.status_code == 507 and code(r) == "PHOTO_STORAGE_FULL"
      and counts() == before, (r.status_code, code(r)))
check("…the error names no patient, key or size of other patients' data", not [w for w in PII + ["photos/"] if w in full_body])
check("…same key again → the 507 is replayed (no write)", upload(pt, nxt, key=key)[0].status_code == 507 and counts() == before)
check("storage full: a text-only skin report still works (201)", report(pt).status_code == 201)
check("storage full: chat still works (patient message 201)", c.post("/api/v1/patients/me/messages", json={"body": "照片上傳不了，先用文字說明"},
                                                                     headers=H(pt)).status_code == 201)
check("storage full: images already stored are still readable", content(nt, A).status_code == 200)
storage.quota_bytes = used + need
r, _ = upload(pt, nxt)
check("quota = used + needed exactly → 201 (the limit itself is allowed)", r.status_code == 201)
def sized():
    data = jpeg()
    with app.app_context():
        im_ = photo_services.prepare_upload(data, "image/jpeg")
        return data, len(im_.full) + len(im_.thumb), storage.used_bytes()


log_lines.clear()
data, need, used = sized()
storage.quota_bytes, storage._last_warning = int((used + need) / 0.75), 0.0  # after this upload: 75%
r1, _ = upload(pt, data)
warn = [ln for ln in log_lines if "photo storage at" in ln]
check("upload crossing PHOTO_DB_WARN_PERCENT (70%) → 201 and one 'warning' line in the log, no patient data",
      r1.status_code == 201 and len(warn) == 1 and "75%" in warn[0] and "warning" in warn[0] and not [w for w in PII if w in warn[0]], warn)
data, need, used = sized()
storage.quota_bytes = int((used + need) / 0.9)  # 90%: critical, but within the hour of the last warning
upload(pt, data)
check("…not repeated within the hour", len([ln for ln in log_lines if "photo storage at" in ln]) == 1)
data, need, used = sized()
storage.quota_bytes, storage._last_warning = int((used + need) / 0.9), 0.0
upload(pt, data)
crit = [ln for ln in log_lines if "photo storage at" in ln][-1]
check("crossing PHOTO_DB_CRITICAL_PERCENT (85%) → a 'critical' line", "90%" in crit and "critical" in crit, crit)

runner = app.test_cli_runner()
with app.app_context():
    storage.quota_bytes = int(storage.used_bytes() / 0.8)
res = runner.invoke(args=["photos", "storage-report"])
check("`flask photos storage-report` at ~80% → warning level, exit code 1", res.exit_code == 1 and "level:           warning" in res.output, res.output)
check("…shows image counts, photo bytes against the quota; no patient, photo id or key in the output",
      "full images:" in res.output and "photo bytes:" in res.output and "of quota" in res.output
      and not [w for w in PII + ["photos/", A, B] if w in res.output])
storage.quota_bytes = saved_quota
res = runner.invoke(args=["photos", "storage-report"])
check("…with the 200 MiB quota → ok, exit code 0", res.exit_code == 0 and "level:           ok" in res.output)
with app.app_context():
    storage.quota_bytes = storage.used_bytes()
res = runner.invoke(args=["photos", "storage-report"])
storage.quota_bytes = saved_quota
check("…at 100% → critical, exit code 2", res.exit_code == 2 and "critical" in res.output)

# ================================================================ verify
with app.app_context():
    rep = photo_services.verify_storage()
check("`photos verify`: nothing missing, no leftovers, no images without a row", not rep["missing"] and rep["leftovers"] == 0 and rep["orphans"] == 0, rep)
with app.app_context():
    a_row = db.session.query(SymptomPhoto).filter_by(public_id=A).one()
    saved_blob = db.session.query(SymptomPhotoBlob).filter_by(photo_id=a_row.id, variant="full").one()
    keep = {k: getattr(saved_blob, k) for k in ("photo_id", "variant", "storage_key", "content_type", "byte_size", "sha256", "data")}
    db.session.delete(saved_blob)
    db.session.commit()
    rep = photo_services.verify_storage()
r = content(nt, A)
check("an image row deleted outside the app → verify reports it missing; the endpoint answers 404 PHOTO_FILE_MISSING",
      [m["variant"] for m in rep["missing"]] == ["full"] and r.status_code == 404 and code(r) == "PHOTO_FILE_MISSING")
with app.app_context():
    db.session.add(SymptomPhotoBlob(**keep))
    db.session.commit()
check("…restored → readable again", content(nt, A).status_code == 200)

# ================================================================ access
check("another patient → 404", content(p2t, A).status_code == 404)
check("a nurse not assigned → 404", content(nt2, A).status_code == 404)
check("admin → 403", content(at, A).status_code == 403)
check("no token → 401", content(None, A).status_code == 401)
check("another patient / unassigned nurse cannot list the photos", c.get(f"/api/v1/patients/{pid}/symptom-photos", headers=H(p2t)).status_code in (403, 404)
      and c.get(f"/api/v1/patients/{pid}/symptom-photos", headers=H(nt2)).status_code in (403, 404))
with app.app_context():
    a_ = db.session.query(NursePatientAssignment).filter(NursePatientAssignment.patient.has(public_id=pid), NursePatientAssignment.ended_at.is_(None)).first()
    a_.ended_at = utcnow()
    db.session.commit()
    ended = content(nt, A).status_code
    a_.ended_at = None
    db.session.commit()
check("assignment ended → the nurse gets 404 at once", ended == 404)
history = c.get(f"/api/v1/patients/{pid}/symptom-photos", headers=H(nt)).get_data(as_text=True)
check("history response: no storage key, no bytes, no URL outside the API", "photos/" not in history and "storage_key" not in history
      and "http" not in history)
with app.app_context():
    total = db.session.query(func.count(SymptomPhotoBlob.id)).scalar()
print(f"[INFO] {total} image rows stored in this run")

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
