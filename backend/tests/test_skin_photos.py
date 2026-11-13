"""皮膚照片 (Phase 1b): consent (versioned draft, accept / revoke), upload (pending → attached in the report's
transaction), image safety (type from content, declared-type mismatch, HEIC message, size / pixel / decode
limits, animated, EXIF + GPS removed, orientation, thumbnails), idempotency and duplicates, partial failure
and compensation, clean-up of orphan photos, access (patient / assigned nurse / ended assignment / other
patient / unassigned nurse / admin), no public URL, response headers, audit without file names, history and
filters, safe disabling (no backend / local in staging / object storage not configured / consent not
approved), the storage adapters, rate limit, regressions (daily report) and the migration — on throw-away
databases and a temporary storage directory only."""
import io
import json
import logging
import os
import struct
import sys
import tempfile
import uuid
import warnings
import zlib
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("error")

from PIL import Image
from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models import AuditLog, NursePatientAssignment, PatientConsent, PatientProfile, Role, SymptomPhoto, SymptomRecord, User
from app.models.base import utcnow
from app.modules.photo import consent as consent_text
from app.modules.photo import services as photo_services
from app.seeds.dev import seed_dev_data
from app.services import photo_storage
from app.services.photo_storage import LocalPrivateStorage, PhotoFeature, PrivateObjectStorage, init_photos

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


app = create_app("testing")
c = app.test_client()
storage = app.extensions["photo_feature"].storage
RECORDS = "/api/v1/symptoms/records"
SKIN = "rt_skin_self_assessment"

# every log line written during the run (photo information must never appear in it)
log_lines = []


class _Collect(logging.Handler):
    def emit(self, record):
        log_lines.append(record.getMessage())


logging.getLogger().addHandler(_Collect())
logging.getLogger().setLevel(logging.DEBUG)


def token(email, password="Demo@1234"):
    return c.post("/api/v1/auth/login", json={"email": email, "password": password}).get_json()["data"]["access_token"]


def H(t, key=None):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": key or str(uuid.uuid4())}


# ---------------------------------------------------------------- synthetic images
def jpeg(size=(2048, 1365), color=(200, 120, 110), exif=None, quality=90):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=quality, **({"exif": exif} if exif is not None else {}))
    return buf.getvalue()


def png(size=(800, 600), mode="RGBA"):
    buf = io.BytesIO()
    Image.new(mode, size, (10, 120, 200, 128) if mode == "RGBA" else (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def webp(size=(800, 600), frames=1):
    buf = io.BytesIO()
    imgs = [Image.new("RGB", size, (i * 40, 100, 100)) for i in range(frames)]
    imgs[0].save(buf, "WEBP", save_all=frames > 1, append_images=imgs[1:])
    return buf.getvalue()


def png_header_only(w, h):
    """A PNG claiming w × h pixels with almost no data (a decompression bomb's header)."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 1, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00" * 64)) + chunk(b"IEND", b"")


def gps_exif():
    ex = Image.Exif()
    ex[0x010F] = "TestMaker"
    ex[0x0110] = "TestPhone-X"
    ex[0x8825] = {1: "N", 2: (25.0, 2.0, 0.0), 3: "E", 4: (121.0, 33.0, 0.0)}
    return ex


def upload(t, data, ctype="image/jpeg", key=None, pid="me", name="IMG_0001.jpg", extra=None):
    files = {"file": (io.BytesIO(data), name, ctype)}
    if extra:
        files.update(extra)
    r = c.post(f"/api/v1/patients/{pid}/symptom-photos", data=files, headers=H(t, key), content_type="multipart/form-data")
    return r, (r.get_json(silent=True) or {})


def code(r):
    return (r.get_json(silent=True) or {}).get("error", {}).get("code")


def content(t, pid_, variant="thumb"):
    return c.get(f"/api/v1/symptom-photos/{pid_}/content?variant={variant}", headers={"Authorization": f"Bearer {t}"})


def report(t, photos=None, key=None, **answers):
    answers = answers or {"skin_itch": "yes"}
    body = {"patient_id": "me", "form_code": SKIN,
            "values": [{"definition_code": k, ("value_numeric" if isinstance(v, int) else "option_code"): v} for k, v in answers.items()]}
    if photos is not None:
        body["photos"] = photos
    r = c.post(RECORDS, json=body, headers=H(t, key))
    return r, (r.get_json(silent=True) or {})


def stored_count():
    return len(storage.keys())


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
    pt, p2t, nt, nt2, at = (token(e) for e in ("patient01@demo.local", "p2@demo.local", "nurse01@demo.local", "nurse02@demo.local", "admin01@demo.local"))
    p1 = db.session.query(PatientProfile).filter_by(patient_code="P00001").one()
    pid = p1.public_id

    # ================================================================ storage location: private, not public
    root = Path(storage.root)
    project = Path(__file__).resolve().parents[2]
    check("test storage is a private temporary directory, outside the project (never a public / static folder)",
          isinstance(storage, LocalPrivateStorage) and project not in root.parents and "static" not in root.parts and "public" not in root.parts, root)

    # ================================================================ settings + consent
    s = c.get("/api/v1/symptom-photos/settings", headers=H(pt)).get_json()["data"]
    cons = s["consent"]
    check("settings (patient): uploads on in testing, 3 per report, JPEG / PNG / WebP, HEIC not supported", s["upload_enabled"] and s["view_enabled"]
          and s["max_per_record"] == 3 and s["accepted_types"] == ["image/jpeg", "image/png", "image/webp"] and s["heic_supported"] is False)
    check("consent text: versioned draft 待院方審核, not yet accepted", cons["version"] == consent_text.CONSENT_VERSION and cons["review_status"] == "draft"
          and "不具法律效力" in cons["notice"] and cons["accepted"] is False)
    text = json.dumps(cons, ensure_ascii=False)
    check("consent covers purpose, who may view, not real-time, upload ≠ reviewed, 119", all(w in text for w in (
        "照片用途", "目前負責照護您的醫療人員", "不會立即有人查看", "不代表醫護人員已經看過", "119")))
    check("consent does not claim the image is anonymous", "畫面本身仍可能辨識出您" in text)
    sn = c.get("/api/v1/symptom-photos/settings", headers=H(nt)).get_json()["data"]
    check("settings (nurse): no consent block", "consent" not in sn and sn["view_enabled"])
    check("settings (admin) → 403", c.get("/api/v1/symptom-photos/settings", headers=H(at)).status_code == 403)
    check("body sites listed and marked pending clinical review", s["body_sites_pending_review"] and {"code": "head_neck", "label": "頭頸部"} in s["body_sites"])

    r, body = upload(pt, jpeg())
    check("upload before consent → 403 PHOTO_CONSENT_REQUIRED, nothing stored", r.status_code == 403 and code(r) == "PHOTO_CONSENT_REQUIRED" and stored_count() == 0)
    r = c.post("/api/v1/patients/me/photo-consent", json={"version": "old"}, headers=H(pt))
    check("accept another version → 409 CONSENT_VERSION_MISMATCH", r.status_code == 409 and code(r) == "CONSENT_VERSION_MISMATCH")
    r = c.post("/api/v1/patients/me/photo-consent", json={"version": consent_text.CONSENT_VERSION}, headers=H(pt))
    check("accept the current version → 201, accepted", r.status_code == 201 and r.get_json()["data"]["accepted"] is True)
    r = c.post("/api/v1/patients/me/photo-consent", json={"version": consent_text.CONSENT_VERSION}, headers=H(pt))
    row = db.session.query(PatientConsent).filter_by(patient_id=p1.id).one()
    check("accepting again → 200, still one consent row, with the text's SHA-256", r.status_code == 200
          and row.content_sha256 == consent_text.content_sha256() and row.revoked_at is None)
    check("nurse cannot accept for a patient → 403", c.post("/api/v1/patients/me/photo-consent", json={"version": consent_text.CONSENT_VERSION}, headers=H(nt)).status_code == 403)
    c.post("/api/v1/patients/me/photo-consent", json={"version": consent_text.CONSENT_VERSION}, headers=H(p2t))

    # ================================================================ upload: re-encode, EXIF / GPS removed, thumbnails
    original = jpeg(exif=gps_exif())
    check("(fixture) the original carries GPS and device EXIF", dict(Image.open(io.BytesIO(original)).getexif().get_ifd(0x8825)) and b"TestPhone-X" in original)
    key = str(uuid.uuid4())
    r, body = upload(pt, original, key=key, name="../../etc/IMG_0001.jpg")
    photo1 = body.get("data", {})
    check("upload JPEG 2048×1365 (as the app resizes it) → 201 pending, resized to ≤ 1600 px", r.status_code == 201 and photo1["status"] == "pending"
          and max(photo1["width"], photo1["height"]) == 1600 and photo1["content_type"] == "image/jpeg", body)
    row1 = db.session.query(SymptomPhoto).filter_by(public_id=photo1["id"]).one()
    check("random, unpredictable ids; storage key never derived from the file name", uuid.UUID(photo1["id"]).version == 4
          and photo_storage.KEY_PATTERN.match(row1.storage_key) and "IMG" not in row1.storage_key and "etc" not in row1.storage_key
          and photo1["id"].replace("-", "") not in row1.storage_key)
    stored_full = storage.get(row1.storage_key)
    stored_im = Image.open(io.BytesIO(stored_full))
    check("stored image: no EXIF at all (no GPS, no device), no XMP / ICC", len(stored_im.getexif()) == 0 and b"TestPhone-X" not in stored_full
          and b"Exif" not in stored_full and "icc_profile" not in stored_im.info and "xmp" not in stored_im.info)
    check("stored checksum = SHA-256 of the stored file", __import__("hashlib").sha256(stored_full).hexdigest() == row1.checksum_sha256)
    r = content(pt, photo1["id"], "full")
    check("patient previews own pending photo (full) → 200 image/jpeg, no metadata in the response", r.status_code == 200
          and r.mimetype == "image/jpeg" and b"TestPhone-X" not in r.data and len(Image.open(io.BytesIO(r.data)).getexif()) == 0)
    check("response headers: no-store, nosniff, inline without the file name, CSP sandbox, CORP same-origin",
          r.headers["Cache-Control"].startswith("private, no-store") and r.headers["X-Content-Type-Options"] == "nosniff"
          and r.headers["Content-Disposition"] == 'inline; filename="photo.jpg"' and "sandbox" in r.headers["Content-Security-Policy"]
          and r.headers["Cross-Origin-Resource-Policy"] == "same-origin", dict(r.headers))
    thumb = Image.open(io.BytesIO(content(pt, photo1["id"], "thumb").data))
    check("thumbnail ≤ 320 px", max(thumb.size) == 320, thumb.size)

    ex = Image.Exif()
    ex[0x0112] = 6  # rotate 90° on display
    r, body = upload(pt, jpeg(size=(400, 200), exif=ex))
    check("EXIF orientation applied before the metadata is dropped (400×200 rotated → 200×400)", r.status_code == 201
          and (body["data"]["width"], body["data"]["height"]) == (200, 400), body.get("data"))
    rot_id = body["data"]["id"]
    r, body = upload(pt, png(), ctype="image/png", name="a.png")
    check("PNG with transparency → 201, stored as JPEG", r.status_code == 201 and body["data"]["content_type"] == "image/jpeg")
    png_id = body["data"]["id"]
    r, body = upload(pt, webp(), ctype="image/webp", name="a.webp")
    check("WebP → 201", r.status_code == 201)
    webp_id = body["data"]["id"]

    # idempotency + duplicates
    rows_before, files_before = db.session.query(SymptomPhoto).count(), stored_count()
    r, body = upload(pt, original, key=key, name="../../etc/IMG_0001.jpg")
    check("same Idempotency-Key + same file → same photo, replayed, nothing new", r.status_code == 201 and body["data"]["id"] == photo1["id"]
          and r.headers.get("Idempotent-Replayed") == "true" and db.session.query(SymptomPhoto).count() == rows_before and stored_count() == files_before)
    r, body = upload(pt, png(), ctype="image/png", key=key)
    check("same Idempotency-Key + another file → 422 IDEMPOTENCY_KEY_MISMATCH", r.status_code == 422 and code(r) == "IDEMPOTENCY_KEY_MISMATCH")
    r, body = upload(pt, original)
    check("same picture again (new key) while pending → 200 the same photo, no second copy", r.status_code == 200 and body["data"]["id"] == photo1["id"]
          and db.session.query(SymptomPhoto).count() == rows_before and stored_count() == files_before)

    # ================================================================ rejected files: nothing stored
    rows_before, files_before = db.session.query(SymptomPhoto).count(), stored_count()
    cases = [
        ("PNG declared as image/jpeg (forged MIME type)", lambda: upload(pt, png(), ctype="image/jpeg"), 415, "PHOTO_TYPE_MISMATCH"),
        ("HTML declared as image/jpeg", lambda: upload(pt, b"<html><script>alert(1)</script></html>" * 50, ctype="image/jpeg"), 415, "PHOTO_UNSUPPORTED_TYPE"),
        ("SVG", lambda: upload(pt, b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>', ctype="image/svg+xml", name="a.svg"), 415, "PHOTO_UNSUPPORTED_TYPE"),
        ("HEIC (iPhone)", lambda: upload(pt, b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 200, ctype="image/heic", name="IMG_1.HEIC"), 415, "PHOTO_HEIC_NOT_SUPPORTED"),
        ("GIF", lambda: upload(pt, b"GIF89a" + b"\x00" * 200, ctype="image/gif", name="a.gif"), 415, "PHOTO_UNSUPPORTED_TYPE"),
        ("truncated JPEG (decode failure)", lambda: upload(pt, jpeg()[:600]), 422, "PHOTO_DECODE_FAILED"),
        ("7000×7000 header (pixel limit, checked before decoding)", lambda: upload(pt, png_header_only(7000, 7000), ctype="image/png"), 413, "PHOTO_TOO_MANY_PIXELS"),
        ("30000×30000 header (decompression bomb)", lambda: upload(pt, png_header_only(30000, 30000), ctype="image/png"), 413, "PHOTO_TOO_MANY_PIXELS"),
        ("animated WebP", lambda: upload(pt, webp(frames=3), ctype="image/webp"), 415, "PHOTO_ANIMATED_NOT_SUPPORTED"),
        ("too small (32 px)", lambda: upload(pt, jpeg(size=(32, 32))), 422, "PHOTO_TOO_SMALL"),
        ("10 MB + 1 byte", lambda: upload(pt, b"\xff\xd8\xff" + b"\x00" * (10 * 1024 * 1024 - 2)), 413, "PHOTO_TOO_LARGE"),
        ("11 MB request", lambda: upload(pt, b"\xff\xd8\xff" + os.urandom(11 * 1024 * 1024)), 413, "PHOTO_TOO_LARGE"),
        ("empty file", lambda: upload(pt, b""), 400, "PHOTO_EMPTY"),
        ("two files in one request", lambda: upload(pt, jpeg(), extra={"other": (io.BytesIO(jpeg()), "b.jpg", "image/jpeg")}), 400, "VALIDATION_ERROR"),
    ]
    for label, call, status, err in cases:
        r, body = call()
        check(f"rejected: {label} → {status} {err}", r.status_code == status and code(r) == err, (r.status_code, code(r)))
    r = c.post("/api/v1/patients/me/symptom-photos", data={}, headers=H(pt), content_type="multipart/form-data")
    check("rejected: no file → 400", r.status_code == 400)
    check("…no row and no stored file from any rejected upload", db.session.query(SymptomPhoto).count() == rows_before and stored_count() == files_before)

    # ================================================================ memory: pixel limit (every format) and one decode at a time
    check("pixel limit = 2048 × 2048 (what the app's resize produces), one image decoded at a time per process",
          app.config["PHOTO_MAX_PIXELS"] == 2048 * 2048 and app.config["PHOTO_MAX_CONCURRENT_PROCESSING"] == 1)

    def solid(fmt, size, **kw):
        buf = io.BytesIO()
        Image.new("RGB", size, (90, 140, 60)).save(buf, fmt, **kw)
        return buf.getvalue()

    over = [("JPEG", "image/jpeg", solid("JPEG", (2049, 2048), quality=80)), ("PNG", "image/png", solid("PNG", (2049, 2048))),
            ("WebP", "image/webp", solid("WEBP", (2049, 2048), quality=60))]
    bombs = [("JPEG", "image/jpeg", solid("JPEG", (6000, 6000), quality=30)), ("WebP", "image/webp", solid("WEBP", (6000, 6000), lossless=True))]
    for label, ctype, data in over:
        r, _ = upload(pt, data, ctype=ctype)
        check(f"rejected: {label} 2049×2048 (one pixel column over the limit) → 413 PHOTO_TOO_MANY_PIXELS", r.status_code == 413
              and code(r) == "PHOTO_TOO_MANY_PIXELS", (r.status_code, code(r)))
    for label, ctype, data in bombs:
        r, _ = upload(pt, data, ctype=ctype)
        check(f"rejected: {label} 6000×6000 in {len(data) // 1024} KB (compressed bomb) → 413 before decoding", r.status_code == 413
              and code(r) == "PHOTO_TOO_MANY_PIXELS" and len(data) < 1024 * 1024, (r.status_code, code(r)))
    check("…no row and no stored file from the oversized images", db.session.query(SymptomPhoto).count() == rows_before and stored_count() == files_before)
    for label, ctype, data in [("JPEG", "image/jpeg", solid("JPEG", (2048, 2048), quality=80)), ("PNG", "image/png", solid("PNG", (2048, 2048))),
                               ("WebP", "image/webp", solid("WEBP", (2048, 2048), quality=60))]:
        image = photo_services.prepare_upload(data, ctype)
        check(f"{label} exactly 2048×2048 (the limit) → accepted, re-encoded to ≤ 1600 px", max(image.width, image.height) == 1600)

    import threading
    import time
    real_process, active, peak = photo_services.process, [0], [0]
    gate = threading.Lock()

    def counting_process(*a, **kw):
        with gate:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        try:
            return real_process(*a, **kw)
        finally:
            with gate:
                active[0] -= 1

    photo_services.process = counting_process
    sample = jpeg(size=(800, 600))

    def work():
        with app.app_context():
            photo_services.prepare_upload(sample, "image/jpeg")

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    photo_services.process = real_process
    check("6 uploads processed concurrently in one process → never more than 1 image decoded at a time", peak[0] == 1, peak[0])

    slots = photo_services._slots()
    slots.acquire()  # simulate a long decode in another thread
    wait_before = app.config["PHOTO_PROCESSING_WAIT_SECONDS"]
    app.config["PHOTO_PROCESSING_WAIT_SECONDS"] = 0.2
    try:
        r, _ = upload(pt, jpeg(size=(800, 600), color=(1, 2, 3)))
    finally:
        app.config["PHOTO_PROCESSING_WAIT_SECONDS"] = wait_before
        slots.release()
    check("decoding slot busy longer than the wait → 503 PHOTO_STORAGE_UNAVAILABLE (retryable), nothing stored",
          r.status_code == 503 and code(r) == "PHOTO_STORAGE_UNAVAILABLE"
          and db.session.query(SymptomPhoto).count() == rows_before and stored_count() == files_before, (r.status_code, code(r)))
    r, _ = upload(nt, jpeg(), pid=pid)
    check("nurse cannot upload → 403", r.status_code == 403)
    r, _ = upload(pt, jpeg(), pid=p2.public_id)
    check("patient uploading for another patient → 404", r.status_code == 404)
    r = c.post("/api/v1/patients/me/symptom-photos", data={"file": (io.BytesIO(jpeg()), "a.jpg", "image/jpeg")},
               headers={"Authorization": f"Bearer {pt}"}, content_type="multipart/form-data")
    check("upload without Idempotency-Key → 428", r.status_code == 428)

    # ================================================================ attach with the report (one transaction)
    before = db.session.query(SymptomRecord).count()
    r, body = report(pt, photos=[{"id": photo1["id"], "body_site": "head_neck"}], skin_itch="maybe")
    check("report with an invalid answer → 400, no record, the photo stays pending (partial failure: nothing lost)", r.status_code == 400
          and db.session.query(SymptomRecord).count() == before and db.session.query(SymptomPhoto).filter_by(public_id=photo1["id"]).one().status == "pending")
    r, body = report(pt, photos=[{"id": i} for i in (photo1["id"], rot_id, png_id, webp_id)])
    check("4 photos → 400 (3 per report)", r.status_code == 400 and "at most 3" in json.dumps(body))
    r, body = report(pt, photos=[{"id": photo1["id"]}, {"id": photo1["id"]}])
    check("the same photo twice → 400", r.status_code == 400)
    r, body = report(pt, photos=[{"id": photo1["id"], "body_site": "elbow"}])
    check("unknown body site → 400", r.status_code == 400)
    r2p, b2p = upload(p2t, jpeg(color=(10, 10, 10)))
    other_photo = b2p["data"]["id"]
    r, body = report(pt, photos=[{"id": other_photo}])
    check("another patient's photo → 422 PHOTO_NOT_FOUND, nothing created", r.status_code == 422 and code(r) == "PHOTO_NOT_FOUND"
          and db.session.query(SymptomRecord).count() == before)
    r = c.post(RECORDS, json={"patient_id": "me", "form_code": "rt_daily_report", "photos": [{"id": photo1["id"]}], "values": [
        {"definition_code": k, ("value_numeric" if isinstance(v, int) else "option_code"): v} for k, v in dict(
            rt_pain_skin=1, rt_pain_oral=1, rt_dermatitis_redness="none", rt_dermatitis_desquamation="none", rt_appetite_poor=1, rt_fatigue=1,
            self_care_moisturizer="yes", self_care_towel_pat="yes", self_care_mouth_rinse="yes").items()]}, headers=H(pt))
    check("daily report cannot take photos → 400 (form unchanged)", r.status_code == 400 and "does not take photos" in json.dumps(r.get_json()))

    rkey = str(uuid.uuid4())
    spec = [{"id": photo1["id"], "body_site": "head_neck"}, {"id": rot_id, "body_site": "head_neck"}, {"id": png_id, "body_site": "axilla_left"}]
    r, body = report(pt, photos=spec, key=rkey, skin_itch="yes", rt_dermatitis_redness="light")
    rec = body.get("data", {})
    check("report + 3 photos → 201, photos attached with their body sites", r.status_code == 201 and len(rec["photos"]) == 3
          and {p["body_site"] for p in rec["photos"]} == {"head_neck", "axilla_left"} and all(p["status"] == "attached" for p in rec["photos"]), body)
    rec_id = rec["id"]
    r, body = report(pt, photos=spec, key=rkey, skin_itch="yes", rt_dermatitis_redness="light")
    check("same Idempotency-Key → same report, photos not attached twice", r.status_code == 201 and body["data"]["id"] == rec_id
          and db.session.query(SymptomPhoto).filter_by(symptom_record_id=rec_id).count() == 3)
    r, body = report(pt, photos=[{"id": photo1["id"]}])
    check("an already attached photo in a new report → 409 PHOTO_NOT_AVAILABLE, no record", r.status_code == 409 and code(r) == "PHOTO_NOT_AVAILABLE")
    r, body = report(pt)
    check("text-only report (no photos) still works → 201, photos []", r.status_code == 201 and body["data"]["photos"] == [])
    audit_attach = db.session.query(AuditLog).filter_by(resource_type="symptom_photos", action="UPDATE", resource_id=photo1["id"]).count()
    check("attachment audited per photo", audit_attach == 1)

    # ================================================================ read access (backend check on every read)
    check("patient: own attached photo → 200", content(pt, photo1["id"]).status_code == 200)
    check("assigned nurse → 200 (thumb and full)", content(nt, photo1["id"]).status_code == 200 and content(nt, photo1["id"], "full").status_code == 200)
    check("another patient → 404", content(p2t, photo1["id"]).status_code == 404)
    check("unassigned nurse → 404", content(nt2, photo1["id"]).status_code == 404)
    check("admin → 403", content(at, photo1["id"]).status_code == 403)
    check("not signed in → 401", c.get(f"/api/v1/symptom-photos/{photo1['id']}/content").status_code == 401)
    check("pending photo of a patient: the assigned nurse cannot see it (not sent yet) → 404", content(nt2, other_photo).status_code == 404
          and content(p2t, other_photo).status_code == 200)
    check("unknown / malformed id → 404", content(pt, str(uuid.uuid4())).status_code == 404 and content(pt, "../../etc/passwd").status_code == 404)
    check("unknown variant → 400", c.get(f"/api/v1/symptom-photos/{photo1['id']}/content?variant=original", headers=H(pt)).status_code == 400)
    check("no public URL: the storage key path and a static path are not served", c.get("/" + row1.storage_key).status_code == 404
          and c.get(f"/static/{row1.storage_key}").status_code == 404 and c.get(f"/api/v1/{row1.storage_key}").status_code == 404)
    views = db.session.query(AuditLog).filter_by(resource_type="symptom_photos", action="VIEW", resource_id=f"{photo1['id']}:full").all()
    check("every image read audited (photo id + variant, nurse and patient)", len(views) >= 2 and {v.actor_role for v in views} >= {"nurse", "patient"})
    all_audit = json.dumps([[a.resource_id, a.changes] for a in db.session.query(AuditLog).all()], ensure_ascii=False, default=str)
    check("audit never contains file names or image data", "IMG_0001" not in all_audit and "etc/" not in all_audit and "TestPhone" not in all_audit)

    assignment = db.session.query(NursePatientAssignment).filter_by(patient_id=p1.id, ended_at=None).first()
    assignment.ended_at = utcnow()
    db.session.commit()
    check("assignment ended → nurse 404 at once (image and list)", content(nt, photo1["id"]).status_code == 404
          and c.get(f"/api/v1/patients/{pid}/symptom-photos", headers=H(nt)).status_code == 404)
    assignment.ended_at = None
    db.session.commit()

    # ================================================================ remove before sending / retention
    r = c.delete(f"/api/v1/symptom-photos/{webp_id}", headers=H(pt))
    wrow = db.session.query(SymptomPhoto).filter_by(public_id=webp_id).one()
    check("patient removes a photo not sent yet → discarded (removed_before_submit), files deleted", r.status_code == 200
          and wrow.status == "discarded" and wrow.delete_reason == "removed_before_submit" and not storage.exists(wrow.storage_key)
          and not storage.exists(wrow.thumb_storage_key))
    check("…and it can no longer be read → 404", content(pt, webp_id).status_code == 404)
    r = c.delete(f"/api/v1/symptom-photos/{photo1['id']}", headers=H(pt))
    check("a sent photo cannot be deleted by the patient → 409 PHOTO_RETAINED (retention pending the hospital), file kept",
          r.status_code == 409 and code(r) == "PHOTO_RETAINED" and storage.exists(row1.storage_key))
    check("nurse / admin cannot delete → 403", c.delete(f"/api/v1/symptom-photos/{photo1['id']}", headers=H(nt)).status_code == 403
          and c.delete(f"/api/v1/symptom-photos/{photo1['id']}", headers=H(at)).status_code == 403)
    check("another patient's photo → 404", c.delete(f"/api/v1/symptom-photos/{other_photo}", headers=H(pt)).status_code == 404)
    check("removal audited (DELETE, reason only)", db.session.query(AuditLog).filter_by(resource_type="symptom_photos", action="DELETE",
                                                                                     resource_id=webp_id).one().changes == {"reason": "removed_before_submit"})

    # ================================================================ orphan clean-up + compensation
    r, body = upload(pt, jpeg(color=(1, 2, 3)))
    old_id = body["data"]["id"]
    old = db.session.query(SymptomPhoto).filter_by(public_id=old_id).one()
    old.created_at -= timedelta(hours=25)
    db.session.commit()
    attached_old = db.session.query(SymptomPhoto).filter_by(public_id=photo1["id"]).one()
    attached_old.created_at -= timedelta(hours=48)
    db.session.commit()
    result = app.test_cli_runner().invoke(args=["photos", "purge-pending"])
    db.session.expire_all()
    old = db.session.query(SymptomPhoto).filter_by(public_id=old_id).one()
    check("`flask photos purge-pending`: a pending photo older than 24 h → discarded (expired_unattached), files deleted",
          result.exit_code == 0 and old.status == "discarded" and old.delete_reason == "expired_unattached" and not storage.exists(old.storage_key), result.output)
    check("…attached photos are never purged (even older ones)", db.session.query(SymptomPhoto).filter_by(public_id=photo1["id"]).one().status == "attached"
          and storage.exists(row1.storage_key))
    check("…recent pending photos kept", db.session.query(SymptomPhoto).filter_by(public_id=other_photo).one().status == "pending")
    check("…clean-up audited as a system action", db.session.query(AuditLog).filter_by(resource_id=old_id, action="DELETE",
                                                                                    actor_identifier="system:photo-cleanup").count() == 1)

    rows_before, files_before = db.session.query(SymptomPhoto).count(), stored_count()
    real_put = storage.put
    calls = {"n": 0}

    def failing_put(key, data, ctype):
        calls["n"] += 1
        if calls["n"] == 2:  # the thumbnail write fails after the full image was written
            raise photo_storage.PhotoStorageError("simulated")
        return real_put(key, data, ctype)

    storage.put = failing_put
    r, body = upload(pt, jpeg(color=(9, 9, 9)))
    storage.put = real_put
    check("storage failure half-way → 503 PHOTO_STORAGE_UNAVAILABLE, the written file removed, no row", r.status_code == 503
          and code(r) == "PHOTO_STORAGE_UNAVAILABLE" and stored_count() == files_before and db.session.query(SymptomPhoto).count() == rows_before)

    real_event = photo_services.record_data_event
    photo_services.record_data_event = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated database failure"))
    try:
        failed = upload(pt, jpeg(color=(8, 8, 8)))[0].status_code == 500  # the app's handler answers 500 …
    except RuntimeError:
        failed = True  # … or the test client propagates it
    photo_services.record_data_event = real_event
    db.session.rollback()
    check("database failure after storing → compensation removes both files, no row", failed and stored_count() == files_before
          and db.session.query(SymptomPhoto).count() == rows_before)

    # ================================================================ rate limit
    app.config["PHOTO_MAX_PER_10_MINUTES"] = 1
    r, _ = upload(pt, jpeg(color=(7, 7, 7)))
    app.config["PHOTO_MAX_PER_10_MINUTES"] = 8
    check("too many uploads → 429 RATE_LIMITED", r.status_code == 429 and code(r) == "RATE_LIMITED")

    # ================================================================ history, filters, dates
    r, body = upload(pt, jpeg(color=(60, 60, 60)))
    later = body["data"]["id"]
    r, body = report(pt, photos=[{"id": later, "body_site": "head_neck"}], rt_dermatitis_redness="dark")
    yesterday_rows = db.session.query(SymptomPhoto).filter_by(symptom_record_id=rec_id).all()
    for p_ in yesterday_rows:
        p_.created_at = utcnow() - timedelta(days=2)
    db.session.commit()
    lst = c.get("/api/v1/patients/me/symptom-photos", headers=H(pt)).get_json()
    items = lst["data"]
    check("patient history: sent photos only, newest first", [i["id"] for i in items][0] == later and len(items) == 4
          and all(i["status"] == "attached" for i in items) and items == sorted(items, key=lambda i: i["created_at"], reverse=True), [i["id"] for i in items])
    check("each photo: local date, body site, its report (time, answers, notes)", items[0]["local_date"] and items[0]["body_site_label"] == "頭頸部"
          and "放射線皮膚炎－發紅情形：深紅" in items[0]["record"]["answers"])
    check("meta: body sites with photos (filter choices)", [s_["code"] for s_ in lst["meta"]["body_sites"]] == ["head_neck", "axilla_left"])
    hn = c.get("/api/v1/patients/me/symptom-photos?body_site=head_neck", headers=H(pt)).get_json()["data"]
    check("filter by body site (same affected area)", len(hn) == 3 and all(i["body_site"] == "head_neck" for i in hn))
    two_days = (date.today() - timedelta(days=2)).isoformat()
    by_date = c.get(f"/api/v1/patients/me/symptom-photos?from={two_days}&to={two_days}", headers=H(pt)).get_json()["data"]
    check("filter by date (one day → the three older photos)", {i["id"] for i in by_date} == {p_.public_id for p_ in yesterday_rows})
    check("two different dates available for side-by-side comparison", len({i["local_date"] for i in items}) == 2)
    check("invalid filters → 400", c.get("/api/v1/patients/me/symptom-photos?body_site=elbow", headers=H(pt)).status_code == 400
          and c.get("/api/v1/patients/me/symptom-photos?from=yesterday", headers=H(pt)).status_code == 400)
    nl = c.get(f"/api/v1/patients/{pid}/symptom-photos", headers=H(nt))
    check("assigned nurse: the same history", nl.status_code == 200 and len(nl.get_json()["data"]) == 4)
    check("unassigned nurse / another patient → 404; admin → 403", c.get(f"/api/v1/patients/{pid}/symptom-photos", headers=H(nt2)).status_code == 404
          and c.get(f"/api/v1/patients/{pid}/symptom-photos", headers=H(p2t)).status_code == 404
          and c.get(f"/api/v1/patients/{pid}/symptom-photos", headers=H(at)).status_code == 403)
    check("list audited (VIEW symptom_photos)", db.session.query(AuditLog).filter_by(resource_type="symptom_photos", action="VIEW", resource_id=pid).count() >= 1)
    nurse_list = c.get(f"{RECORDS}/{pid}?form_code={SKIN}", headers=H(nt)).get_json()["data"]
    check("nurse review list: each report carries its photos (metadata only, no URL)", any(len(x["photos"]) == 3 for x in nurse_list)
          and "url" not in json.dumps(nurse_list) and "storage_key" not in json.dumps(nurse_list))
    mine = c.get(f"{RECORDS}/me?form_code={SKIN}", headers=H(pt)).get_json()["data"]
    check("patient list: own photos on own reports; no storage keys or checksums", any(len(x["photos"]) == 3 for x in mine)
          and "storage_key" not in json.dumps(mine) and "sha256" not in json.dumps(mine))

    # entered in error → photos hidden
    r = c.post(f"{RECORDS}/{rec_id}/mark-error", json={"reason": "傳錯照片"}, headers=H(nt))
    check("report marked entered-in-error → its photos are no longer listed or readable", r.status_code == 200
          and len(c.get("/api/v1/patients/me/symptom-photos", headers=H(pt)).get_json()["data"]) == 1 and content(pt, photo1["id"]).status_code == 404
          and storage.exists(row1.storage_key))

    # ================================================================ consent revoked
    r, body = upload(pt, jpeg(color=(5, 50, 5)))
    pending_after = body["data"]["id"]
    c.post("/api/v1/patients/me/photo-consent/revoke", headers=H(pt))
    r2, _ = upload(pt, jpeg(color=(6, 60, 6)))
    r3, _ = report(pt, photos=[{"id": pending_after}])
    check("consent revoked → upload 403 and attaching 403 (PHOTO_CONSENT_REQUIRED); text reports still fine",
          r2.status_code == 403 and r3.status_code == 403 and code(r3) == "PHOTO_CONSENT_REQUIRED" and report(pt)[0].status_code == 201)
    check("revocation recorded (revoked_at / revoked_by), the old row kept", db.session.query(PatientConsent).filter_by(patient_id=p1.id).one().revoked_at is not None)
    c.post("/api/v1/patients/me/photo-consent", json={"version": consent_text.CONSENT_VERSION}, headers=H(pt))
    check("accepting again → a new consent row (history kept)", db.session.query(PatientConsent).filter_by(patient_id=p1.id).count() == 2)

    # ================================================================ acceptance: consent withdrawal
    from sqlalchemy import text as sql
    from sqlalchemy.sql.dml import Update

    later_row = db.session.query(SymptomPhoto).filter_by(public_id=later).one()
    c.post("/api/v1/patients/me/photo-consent/revoke", headers=H(pt))
    revoked = db.session.query(PatientConsent).filter_by(patient_id=p1.id).order_by(PatientConsent.id.desc()).first()
    rev_audit = db.session.query(AuditLog).filter_by(resource_type="patient_consents", action="UPDATE", resource_id=str(revoked.id)).all()
    check("withdrawal audited: UPDATE patient_consents {revoked, version} by the patient", revoked.revoked_at is not None
          and any(a.changes.get("revoked") is True and a.changes.get("version") == consent_text.CONSENT_VERSION and a.actor_role == "patient" for a in rev_audit))
    r, _ = upload(pt, jpeg(color=(11, 99, 11)))
    check("after withdrawal: new uploads refused at once → 403 PHOTO_CONSENT_REQUIRED", r.status_code == 403 and code(r) == "PHOTO_CONSENT_REQUIRED")
    check("after withdrawal: sent photos are not deleted (row attached, both files kept)", later_row.status == "attached"
          and storage.exists(later_row.storage_key) and storage.exists(later_row.thumb_storage_key)
          and db.session.query(SymptomPhoto).filter(SymptomPhoto.status == "discarded", SymptomPhoto.deleted_at > revoked.revoked_at).count() == 0)
    check("after withdrawal: access unchanged — patient and assigned nurse 200, unassigned nurse 404, admin 403",
          content(pt, later).status_code == 200 and content(nt, later).status_code == 200 and content(nt2, later).status_code == 404
          and content(at, later).status_code == 403)
    r = c.post("/api/v1/patients/me/photo-consent", json={"version": "skin-photo-2025-old"}, headers=H(pt))
    check("re-consent with another version → 409 CONSENT_VERSION_MISMATCH", r.status_code == 409 and code(r) == "CONSENT_VERSION_MISMATCH")
    r = c.post("/api/v1/patients/me/photo-consent", json={"version": consent_text.CONSENT_VERSION}, headers=H(pt))
    created = db.session.query(AuditLog).filter_by(resource_type="patient_consents", action="CREATE", patient_id=p1.id).all()
    check("re-consent with the current version → 201; every acceptance audited with version + text hash", r.status_code == 201 and len(created) == 3
          and all(a.changes["version"] == consent_text.CONSENT_VERSION and a.changes["content_sha256"] == consent_text.content_sha256() for a in created))
    # an acceptance of an older version is replaced by the current one — its revocation audited too
    c.post("/api/v1/patients/me/photo-consent/revoke", headers=H(p2t))
    old = PatientConsent(patient_id=p2.id, consent_type="skin_photo", version="skin-photo-2025-old", content_sha256="0" * 64,
                         accepted_by=u2.id, accepted_at=utcnow())
    db.session.add(old)
    db.session.commit()
    r, _ = upload(p2t, jpeg(color=(12, 12, 120)))
    check("an older accepted version does not allow uploads → 403", r.status_code == 403 and code(r) == "PHOTO_CONSENT_REQUIRED")
    c.post("/api/v1/patients/me/photo-consent", json={"version": consent_text.CONSENT_VERSION}, headers=H(p2t))
    db.session.expire_all()
    sup = db.session.query(AuditLog).filter_by(resource_type="patient_consents", action="UPDATE", resource_id=str(old.id)).one_or_none()
    check("accepting the current version revokes the older one, audited (superseded_by)", db.session.get(PatientConsent, old.id).revoked_at is not None
          and sup is not None and sup.changes.get("superseded_by") == consent_text.CONSENT_VERSION)

    # ================================================================ acceptance: storage ↔ database consistency
    storage.delete(later_row.thumb_storage_key)
    r = content(pt, later)
    check("row present, file missing → 404 PHOTO_FILE_MISSING (not a silent 'no access'), other variant still served",
          r.status_code == 404 and code(r) == "PHOTO_FILE_MISSING" and content(pt, later, "full").status_code == 200)
    check("…the list still works (metadata)", any(i["id"] == later for i in c.get("/api/v1/patients/me/symptom-photos", headers=H(pt)).get_json()["data"]))
    res = app.test_cli_runner().invoke(args=["photos", "verify"])
    check("`flask photos verify` reports the missing file (exit 1), never 'fixes' it", res.exit_code == 1 and "1 missing" in res.output
          and f"missing: photo {later} (attached, thumb)" in res.output, res.output)
    storage.put(later_row.thumb_storage_key, storage.get(later_row.storage_key), "image/jpeg")

    def racing(on_update):
        """Run ``on_update`` right before the first UPDATE of symptom_photos — another request winning the race."""
        original = db.session.execute
        state = {"done": False}

        def execute(stmt, *a, **k):
            if not state["done"] and isinstance(stmt, Update) and stmt.table.name == "symptom_photos":
                state["done"] = True
                on_update(original)
            return original(stmt, *a, **k)
        db.session.execute = execute
        return lambda: db.session.__dict__.pop("execute", None)

    text_rec = report(pt)[1]["data"]["id"]

    def attach_to_other(photo_public_id):
        return lambda run: run(sql("UPDATE symptom_photos SET status = 'attached', symptom_record_id = :r, attached_at = :n WHERE public_id = :p"),
                               {"r": text_rec, "n": utcnow(), "p": photo_public_id})

    r, body = upload(pt, jpeg(color=(30, 140, 30)))
    race_id = body["data"]["id"]
    race_row = db.session.query(SymptomPhoto).filter_by(public_id=race_id).one()
    before = db.session.query(SymptomRecord).count()
    restore = racing(attach_to_other(race_id))
    r, _ = report(pt, photos=[{"id": race_id}])
    restore()
    db.session.rollback()
    check("race: two reports attach the same photo — the loser gets 409 PHOTO_NOT_AVAILABLE and creates nothing",
          r.status_code == 409 and code(r) == "PHOTO_NOT_AVAILABLE" and db.session.query(SymptomRecord).count() == before)

    restore = racing(attach_to_other(race_id))
    r = c.delete(f"/api/v1/symptom-photos/{race_id}", headers=H(pt))
    restore()
    db.session.rollback()
    check("race: removing a photo that a report attaches at the same moment → 409 PHOTO_RETAINED, files kept",
          r.status_code == 409 and code(r) == "PHOTO_RETAINED" and storage.exists(race_row.storage_key) and storage.exists(race_row.thumb_storage_key))

    race_row = db.session.query(SymptomPhoto).filter_by(public_id=race_id).one()
    race_row.created_at -= timedelta(hours=30)
    db.session.commit()
    restore = racing(attach_to_other(race_id))
    n = photo_services.purge_expired_pending(limit=10)
    restore()
    db.session.rollback()
    check("race: clean-up while a report attaches the photo → nothing discarded, files kept", n == 0
          and storage.exists(race_row.storage_key) and storage.exists(race_row.thumb_storage_key))

    attached_before = {p.public_id for p in db.session.query(SymptomPhoto).filter_by(status="attached")}
    real_delete = storage.delete
    storage.delete = lambda key: (_ for _ in ()).throw(photo_storage.PhotoStorageError("simulated"))
    n = photo_services.purge_expired_pending(limit=10)
    storage.delete = real_delete
    db.session.expire_all()
    race_row = db.session.query(SymptomPhoto).filter_by(public_id=race_id).one()
    check("clean-up with a failing file removal: the photo is discarded (committed), its files left behind",
          n == 1 and race_row.status == "discarded" and storage.exists(race_row.storage_key))
    res = app.test_cli_runner().invoke(args=["photos", "purge-pending"])
    check("running the clean-up again is safe: nothing else discarded, attached photos untouched", res.exit_code == 0 and "Discarded 0" in res.output
          and {p.public_id for p in db.session.query(SymptomPhoto).filter_by(status="attached")} == attached_before)
    orphan_key = f"photos/{uuid.uuid4().hex}/full.jpg"
    storage.put(orphan_key, b"not referenced", "image/jpeg")
    res = app.test_cli_runner().invoke(args=["photos", "verify"])
    check("`flask photos verify`: leftovers of discarded photos + files without a row reported (no missing → exit 0)",
          res.exit_code == 0 and "2 leftover" in res.output and "1 file(s) without a row" in res.output, res.output)
    res = app.test_cli_runner().invoke(args=["photos", "verify", "--fix"])
    check("`flask photos verify --fix` removes them; attached photos' files untouched", res.exit_code == 0 and not storage.exists(race_row.storage_key)
          and not storage.exists(orphan_key) and all(storage.exists(p.storage_key) for p in db.session.query(SymptomPhoto).filter_by(status="attached")))
    res = app.test_cli_runner().invoke(args=["photos", "verify"])
    check("…verify is clean afterwards", res.exit_code == 0 and "0 missing" in res.output and "0 leftover" in res.output and "0 file(s) without a row" in res.output, res.output)

    # ================================================================ safe disabling
    real_feature = app.extensions["photo_feature"]
    app.extensions["photo_feature"] = PhotoFeature(upload_enabled=False, view_enabled=False, reason="no_storage_backend", storage=None)
    s = c.get("/api/v1/symptom-photos/settings", headers=H(pt)).get_json()["data"]
    r, _ = upload(pt, jpeg(color=(4, 4, 4)))
    r2, _ = report(pt, photos=[{"id": pending_after}])
    r3, _ = report(pt)
    check("feature off: settings say so; upload 503 PHOTO_UPLOADS_DISABLED; report with photos 503; text report 201",
          s["upload_enabled"] is False and s["reason"] == "no_storage_backend" and r.status_code == 503 and code(r) == "PHOTO_UPLOADS_DISABLED"
          and r2.status_code == 503 and r3.status_code == 201)
    check("feature off: images cannot be read → 503 (no fallback storage)", content(pt, later).status_code == 503)

    # F5: photos off → a consent can be revoked but never recorded (no patient_consents row, no CREATE audit)
    def consent_rows():
        return db.session.query(PatientConsent).filter_by(patient_id=p1.id).count()

    def consent_audits():
        return db.session.query(AuditLog).filter_by(resource_type="patient_consents", action="CREATE").count()

    app.extensions["photo_feature"] = real_feature
    c.post("/api/v1/patients/me/photo-consent", json={"version": consent_text.CONSENT_VERSION}, headers=H(pt))  # (fixture) active consent
    app.extensions["photo_feature"] = PhotoFeature(upload_enabled=False, view_enabled=False, reason="disabled_by_config", storage=None)
    r = c.post("/api/v1/patients/me/photo-consent/revoke", headers=H(pt))
    db.session.expire_all()
    check("feature off: revoking an existing consent still works → 200, not accepted, row revoked",
          r.status_code == 200 and r.get_json()["data"]["accepted"] is False
          and db.session.query(PatientConsent).filter_by(patient_id=p1.id, revoked_at=None).count() == 0)
    rows_before, audits_before = consent_rows(), consent_audits()
    r = c.post("/api/v1/patients/me/photo-consent", json={"version": consent_text.CONSENT_VERSION}, headers=H(pt))
    r_old = c.post("/api/v1/patients/me/photo-consent", json={"version": "skin-photo-2025-old"}, headers=H(pt))
    db.session.expire_all()
    check("feature off: accepting the consent → 503 PHOTO_UPLOADS_DISABLED (any version)",
          r.status_code == 503 and code(r) == "PHOTO_UPLOADS_DISABLED" and r_old.status_code == 503 and code(r_old) == "PHOTO_UPLOADS_DISABLED")
    check("feature off: …no patient_consents row and no consent CREATE audit written",
          consent_rows() == rows_before and consent_audits() == audits_before
          and db.session.query(PatientConsent).filter_by(patient_id=p1.id, revoked_at=None).count() == 0)
    s = c.get("/api/v1/symptom-photos/settings", headers=H(pt)).get_json()["data"]
    check("feature off: settings still report consent not accepted", s["consent"]["accepted"] is False)
    app.extensions["photo_feature"] = real_feature
    r = c.post("/api/v1/patients/me/photo-consent", json={"version": consent_text.CONSENT_VERSION}, headers=H(pt))
    check("feature back on: the patient can accept again → 201", r.status_code == 201 and r.get_json()["data"]["accepted"] is True)

    def status_for(config_name, **cfg):
        fake = SimpleNamespace(config={"CONFIG_NAME": config_name, **cfg}, testing=False, instance_path=tempfile.gettempdir(), extensions={})
        init_photos(fake)
        return fake.extensions["photo_feature"]

    f = status_for("development", PHOTO_UPLOADS_ENABLED=False, PHOTO_STORAGE_BACKEND="local", PHOTO_LOCAL_DIR=tempfile.gettempdir())
    check("PHOTO_UPLOADS_ENABLED off → disabled_by_config", not f.upload_enabled and f.reason == "disabled_by_config")
    f = status_for("development", PHOTO_UPLOADS_ENABLED=True, PHOTO_STORAGE_BACKEND="")
    check("no backend → no_storage_backend (off)", not f.upload_enabled and not f.view_enabled and f.reason == "no_storage_backend")
    f = status_for("staging", PHOTO_UPLOADS_ENABLED=True, PHOTO_STORAGE_BACKEND="local", PHOTO_CONSENT_APPROVED_VERSION=consent_text.CONSENT_VERSION)
    check("staging + local disk → refused (local_storage_not_allowed), no storage at all", not f.upload_enabled and f.storage is None
          and f.reason == "local_storage_not_allowed")
    f = status_for("production", PHOTO_UPLOADS_ENABLED=True, PHOTO_STORAGE_BACKEND="local", PHOTO_CONSENT_APPROVED_VERSION=consent_text.CONSENT_VERSION)
    check("production + local disk → refused", not f.upload_enabled and f.storage is None)
    obj = dict(PHOTO_OBJECT_BUCKET="b", PHOTO_OBJECT_REGION="asia-east1", PHOTO_OBJECT_KMS_KEY_ID="k", PHOTO_OBJECT_ACCESS_KEY_ID="a",
               PHOTO_OBJECT_SECRET_ACCESS_KEY="s")
    f = status_for("staging", PHOTO_UPLOADS_ENABLED=True, PHOTO_STORAGE_BACKEND="object", PHOTO_OBJECT_BUCKET="b")
    check("object storage with missing settings → object_storage_not_configured (off)", not f.upload_enabled and f.reason == "object_storage_not_configured")
    real_sdk = PrivateObjectStorage.sdk_available
    PrivateObjectStorage.sdk_available = staticmethod(lambda: False)
    f = status_for("staging", PHOTO_UPLOADS_ENABLED=True, PHOTO_STORAGE_BACKEND="object", **obj)
    check("object storage configured but no SDK installed → off", not f.upload_enabled and f.reason == "object_storage_not_configured")
    PrivateObjectStorage.sdk_available = staticmethod(lambda: True)
    f = status_for("staging", PHOTO_UPLOADS_ENABLED=True, PHOTO_STORAGE_BACKEND="object", **obj)
    check("staging + object storage, consent text not approved → uploads off (consent_not_approved), stored photos readable",
          not f.upload_enabled and f.view_enabled and f.reason == "consent_not_approved")
    f = status_for("staging", PHOTO_UPLOADS_ENABLED=True, PHOTO_STORAGE_BACKEND="object", PHOTO_CONSENT_APPROVED_VERSION=consent_text.CONSENT_VERSION, **obj)
    check("…approved consent version → on", f.upload_enabled and f.reason is None and isinstance(f.storage, PrivateObjectStorage))
    # production never runs on the draft consent text, even when PHOTO_CONSENT_APPROVED_VERSION names it
    check("(fixture) the current consent text is the draft skin-photo-2026-11-draft-1",
          consent_text.CONSENT_VERSION == "skin-photo-2026-11-draft-1" and consent_text.REVIEW_STATUS == "draft")
    f = status_for("production", PHOTO_UPLOADS_ENABLED=True, PHOTO_STORAGE_BACKEND="object", PHOTO_CONSENT_APPROVED_VERSION="skin-photo-2026-11-draft-1", **obj)
    check("production + PHOTO_CONSENT_APPROVED_VERSION=skin-photo-2026-11-draft-1 → uploads off (consent_not_approved)",
          not f.upload_enabled and f.reason == "consent_not_approved" and f.view_enabled)
    real_text = (consent_text.CONSENT_VERSION, consent_text.REVIEW_STATUS)
    consent_text.CONSENT_VERSION, consent_text.REVIEW_STATUS = "skin-photo-2026-12-v1", "approved"
    f = status_for("production", PHOTO_UPLOADS_ENABLED=True, PHOTO_STORAGE_BACKEND="object", PHOTO_CONSENT_APPROVED_VERSION="skin-photo-2026-12-v1", **obj)
    check("production + a hospital-approved text (review status approved, matching version) → on", f.upload_enabled and f.reason is None)
    f = status_for("production", PHOTO_UPLOADS_ENABLED=True, PHOTO_STORAGE_BACKEND="object", PHOTO_CONSENT_APPROVED_VERSION="skin-photo-2026-11-draft-1", **obj)
    check("production + approved text but the variable still names the draft → off", not f.upload_enabled and f.reason == "consent_not_approved")
    consent_text.CONSENT_VERSION, consent_text.REVIEW_STATUS = "skin-photo-2026-12-draft-2", "approved"
    f = status_for("production", PHOTO_UPLOADS_ENABLED=True, PHOTO_STORAGE_BACKEND="object", PHOTO_CONSENT_APPROVED_VERSION="skin-photo-2026-12-draft-2", **obj)
    check("production + a version string containing 'draft' → off", not f.upload_enabled and f.reason == "consent_not_approved")
    consent_text.CONSENT_VERSION, consent_text.REVIEW_STATUS = real_text
    PrivateObjectStorage.sdk_available = real_sdk
    check("default config (base / staging / production): PHOTO_UPLOADS_ENABLED off, no backend",
          not __import__("app.config", fromlist=["StagingConfig"]).StagingConfig.PHOTO_UPLOADS_ENABLED
          and __import__("app.config", fromlist=["ProductionConfig"]).ProductionConfig.PHOTO_STORAGE_BACKEND in ("", None))

    # ================================================================ adapters
    calls_ = []

    class FakeS3:
        def put_object(self, **kw):
            calls_.append(("put", kw))

        def get_object(self, **kw):
            calls_.append(("get", kw))
            return {"Body": io.BytesIO(b"x")}

        def delete_object(self, **kw):
            calls_.append(("delete", kw))

    objs = PrivateObjectStorage(obj, client=FakeS3())
    k = "photos/" + "a" * 32 + "/full.jpg"
    objs.put(k, b"data", "image/jpeg")
    objs.get(k)
    objs.delete(k)
    put_kw = calls_[0][1]
    check("object adapter: private put with SSE-KMS (customer key), no-store, the given key only", put_kw["ServerSideEncryption"] == "aws:kms"
          and put_kw["SSEKMSKeyId"] == "k" and put_kw["CacheControl"] == "private, no-store" and put_kw["Key"] == k and "ACL" not in put_kw
          and [x[0] for x in calls_] == ["put", "get", "delete"])
    bad = 0
    for key_ in ("../x", "photos/../../etc/passwd", "photos/" + "a" * 32 + "/../full.jpg", "photos/ABC/full.jpg", "/abs/full.jpg", "photos\\x\\full.jpg"):
        for st in (storage, objs):
            try:
                st.get(key_)
            except ValueError:
                bad += 1
    check("path traversal / foreign keys refused by both backends", bad == 12)

    check("no photo information in the application log", not any(w in line for line in log_lines for w in ("IMG_0001", "TestPhone", "etc/IMG")))

# ================================================================ migration: empty database and an existing one
migrations = str(Path(__file__).resolve().parents[1] / "migrations")
for label, existing in (("empty database", False), ("existing database", True)):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        from app.config import DevelopmentConfig
        from flask_migrate import downgrade, upgrade
        from sqlalchemy import inspect, text

        target = f"sqlite:///{Path(tmp, 'mig.db').as_posix()}"
        saved = (DevelopmentConfig.SQLALCHEMY_DATABASE_URI, DevelopmentConfig.EMAIL_CAPTURE_DIR, DevelopmentConfig.PHOTO_LOCAL_DIR)
        DevelopmentConfig.SQLALCHEMY_DATABASE_URI, DevelopmentConfig.EMAIL_CAPTURE_DIR = target, None
        DevelopmentConfig.PHOTO_LOCAL_DIR = str(Path(tmp, "photos"))
        mig = create_app("development")
        DevelopmentConfig.SQLALCHEMY_DATABASE_URI, DevelopmentConfig.EMAIL_CAPTURE_DIR, DevelopmentConfig.PHOTO_LOCAL_DIR = saved
        with mig.app_context(), warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            if str(db.engine.url) != target:
                raise SystemExit(f"refusing to migrate {db.engine.url}: not the throw-away database")
            tables = ("users", "patient_profiles", "nurse_patient_assignments", "symptom_records", "chat_messages", "symptom_forms")
            if existing:
                upgrade(directory=migrations, revision="c3e9a1f4b7d2")  # Phase 1a head
                seed_dev_data(); db.session.commit()
                counts_before = [db.session.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables]
            upgrade(directory=migrations)
            names = set(inspect(db.engine).get_table_names())
            check(f"migration ({label}): creates symptom_photos + patient_consents", {"symptom_photos", "patient_consents"} <= names)
            if existing:
                counts_after = [db.session.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables]
                check(f"migration ({label}): users, patients, assignments, records, chat, forms untouched", counts_after == counts_before, (counts_before, counts_after))
                downgrade(directory=migrations, revision="c3e9a1f4b7d2")
                names = set(inspect(db.engine).get_table_names())
                counts_down = [db.session.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables]
                check(f"migration ({label}): downgrade drops only the two photo tables", not {"symptom_photos", "patient_consents"} & names
                      and counts_down == counts_before)
                upgrade(directory=migrations)
                check(f"migration ({label}): upgrade again works", "symptom_photos" in set(inspect(db.engine).get_table_names()))
            db.session.remove()
            db.engine.dispose()

print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
