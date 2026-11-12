"""皮膚照片 private object storage — failure handling with the real boto3 client against a local fake S3
endpoint (127.0.0.1) that can answer normally, hang, refuse connections, return KMS / access errors or fail
half-way: the central client settings (timeouts, retries, refused when unsafe), 503 without an incomplete
photo record, compensation, error responses and logs without bucket / credentials / KMS key / patient data,
and the clean-up that stops at the first storage failure. No AWS, no moto; synthetic images only.

The fake endpoint imitates S3's wire format closely enough for botocore; it says nothing about AWS itself
(tests/object_storage/photo_object_storage_check.py and docs/skin-photo-deployment.md §6)."""
import io
import logging
import os
import socket
import sys
import threading
import time
import urllib.parse
import uuid
import warnings
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
warnings.simplefilter("ignore", ResourceWarning)  # werkzeug's spooled multipart files (also in test_skin_photos)

from PIL import Image

from app import config as app_config
from app import create_app
from app.extensions import db
from app.models import IdempotencyRecord, PatientProfile, SymptomPhoto
from app.models.base import utcnow
from app.modules.photo import services as photo_services
from app.seeds.dev import seed_dev_data
from app.services.photo_storage import PrivateObjectStorage, init_photos

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))


# ---------------------------------------------------------------- fake S3 endpoint
BUCKET = "ccp-resilience-bucket-7f3a"
ACCOUNT = "111122223333"
KMS_ARN = f"arn:aws:kms:ap-northeast-1:{ACCOUNT}:key/0d7c1b6e-4a7e-4b8e-9d55-2f6f3c1a9e01"
ACCESS_KEY = "test-access-key-resilience"  # synthetic; not in the AWS key format (no secret-scanner false positive)
SECRET_KEY = "resilience-secret-" + uuid.uuid4().hex


class S3:
    objects = {}
    requests = []  # (method, key, headers)
    mode = {}
    hang_seconds = 4.0

    @classmethod
    def reset(cls, **modes):
        cls.requests.clear()
        cls.mode = {"PUT": "ok", "GET": "ok", "DELETE": "ok", "HEAD": "ok", **modes}

    @classmethod
    def count(cls, method, suffix=""):
        return sum(1 for m, k, _ in cls.requests if m == method and k.endswith(suffix))


ERRORS = {  # what S3 answers (shape of real responses; the messages carry ARNs and the bucket on purpose)
    "kms_denied": (403, "AccessDenied", f"User: arn:aws:iam::{ACCOUNT}:user/ccp-photos is not authorized to perform: "
                                        f"kms:GenerateDataKey on resource: {KMS_ARN} (bucket {BUCKET})"),
    "kms_decrypt_denied": (403, "AccessDenied", f"User: arn:aws:iam::{ACCOUNT}:user/ccp-photos is not authorized to perform: "
                                                f"kms:Decrypt on resource: {KMS_ARN}"),
    "kms_disabled": (400, "KMS.DisabledException", f"{KMS_ARN} is disabled."),
    "internal": (500, "InternalError", "We encountered an internal error. Please try again."),
    "no_bucket": (404, "NoSuchBucket", f"The specified bucket does not exist: {BUCKET}"),
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _key(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        return path.split(f"/{BUCKET}/", 1)[-1]

    def _send(self, status, body=b"", headers=None):
        try:
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)) if self.command != "HEAD" else "0")
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
        except OSError:
            pass  # the client gave up (timeout)

    def _handle(self):
        key = self._key()
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if self.command == "PUT" else b""
        S3.requests.append((self.command, key, {k.lower(): v for k, v in self.headers.items()}))
        mode = S3.mode.get(self.command, "ok")
        if callable(mode):
            mode = mode(key)
        if mode == "hang":
            time.sleep(S3.hang_seconds)
            self._send(500)
            return
        if mode in ERRORS:
            status, code, message = ERRORS[mode]
            xml = (f'<?xml version="1.0" encoding="UTF-8"?><Error><Code>{code}</Code><Message>{message}</Message>'
                   f"<RequestId>REQ{len(S3.requests)}</RequestId></Error>").encode()
            self._send(status, xml, {"Content-Type": "application/xml"})
            return
        if self.command == "PUT":
            S3.objects[key] = body
            self._send(200, b"", {"ETag": '"etag"', "x-amz-server-side-encryption": "aws:kms"})
        elif self.command in ("GET", "HEAD"):
            if key in S3.objects:
                self._send(200, S3.objects[key], {"Content-Type": "image/jpeg"})
            else:
                xml = b'<?xml version="1.0" encoding="UTF-8"?><Error><Code>NoSuchKey</Code><Message>none</Message></Error>'
                self._send(404, xml, {"Content-Type": "application/xml"})
        elif self.command == "DELETE":
            S3.objects.pop(key, None)
            self._send(204)

    do_PUT = do_GET = do_HEAD = do_DELETE = _handle


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
ENDPOINT = f"http://127.0.0.1:{server.server_address[1]}"


def closed_port():
    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        return s_.getsockname()[1]


# ---------------------------------------------------------------- app on the fake endpoint
log_lines = []


class _Collect(logging.Handler):
    def emit(self, record):
        log_lines.append(record.getMessage())


logging.getLogger().addHandler(_Collect())
logging.getLogger().setLevel(logging.INFO)

CONNECT, READ, ATTEMPTS = 0.5, 1.0, 2
OBJECT = dict(PHOTO_STORAGE_BACKEND="object", PHOTO_OBJECT_BUCKET=BUCKET, PHOTO_OBJECT_REGION="ap-northeast-1",
              PHOTO_OBJECT_ENDPOINT_URL=ENDPOINT, PHOTO_OBJECT_KMS_KEY_ID=KMS_ARN, PHOTO_OBJECT_ACCESS_KEY_ID=ACCESS_KEY,
              PHOTO_OBJECT_SECRET_ACCESS_KEY=SECRET_KEY, PHOTO_OBJECT_CONNECT_TIMEOUT=CONNECT,
              PHOTO_OBJECT_READ_TIMEOUT=READ, PHOTO_OBJECT_MAX_ATTEMPTS=ATTEMPTS)
BOUND = ATTEMPTS * (CONNECT + READ) + 2.0  # + botocore's retry back-off (jitter) and request handling

app = create_app("testing")
app.config.update(OBJECT, PHOTO_MAX_PER_10_MINUTES=1000, PHOTO_MAX_PER_DAY=1000)
init_photos(app)
c = app.test_client()
responses = []  # every error response body: scanned for leaks at the end
FORBIDDEN = [BUCKET, ACCOUNT, KMS_ARN, ACCESS_KEY, SECRET_KEY, "kms:", "arn:aws", "photos/", "patient01@demo.local", "P00001"]


def leaks(text):
    return [w for w in FORBIDDEN if w in text]


def H(t, key=None):
    return {"Authorization": f"Bearer {t}", "Idempotency-Key": key or str(uuid.uuid4())}


def code(r):
    return (r.get_json(silent=True) or {}).get("error", {}).get("code")


def jpeg():
    """A distinct synthetic picture each time (random noise: neighbouring flat colours can encode to identical
    JPEG bytes, which the app rightly treats as the same pending photo)."""
    buf = io.BytesIO()
    Image.frombytes("RGB", (48, 36), os.urandom(48 * 36 * 3)).resize((900, 700)).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def upload(t, key=None, data=None):
    start = time.monotonic()
    r = c.post("/api/v1/patients/me/symptom-photos", data={"file": (io.BytesIO(data or jpeg()), "IMG_0001.jpg", "image/jpeg")},
               headers=H(t, key), content_type="multipart/form-data")
    elapsed = time.monotonic() - start
    if r.status_code >= 400:
        responses.append(r.get_data(as_text=True))
    return r, elapsed


def content(t, photo_id):
    start = time.monotonic()
    r = c.get(f"/api/v1/symptom-photos/{photo_id}/content?variant=full", headers={"Authorization": f"Bearer {t}"})
    if r.status_code >= 400:
        responses.append(r.get_data(as_text=True))
    return r, time.monotonic() - start


def rows():
    with app.app_context():
        return db.session.query(SymptomPhoto).count()


def reinit(**cfg):
    app.config.update(OBJECT, **cfg)
    init_photos(app)
    return app.extensions["photo_feature"]


with app.app_context():
    db.create_all()
    seed_dev_data()
    db.session.commit()
    pid =db.session.query(PatientProfile).filter_by(patient_code="P00001").one().public_id
pt = c.post("/api/v1/auth/login", json={"email": "patient01@demo.local", "password": "Demo@1234"}).get_json()["data"]["access_token"]
version = c.get("/api/v1/symptom-photos/settings", headers=H(pt)).get_json()["data"]["consent"]["version"]
c.post("/api/v1/patients/me/photo-consent", json={"version": version}, headers=H(pt))

# ================================================================ central client settings
check("defaults (app/config.py): connect 3 s, read 5 s, 2 attempts in total",
      (app_config.Config.PHOTO_OBJECT_CONNECT_TIMEOUT, app_config.Config.PHOTO_OBJECT_READ_TIMEOUT, app_config.Config.PHOTO_OBJECT_MAX_ATTEMPTS) == (3.0, 5.0, 2))
default_client = PrivateObjectStorage({"PHOTO_OBJECT_REGION": "ap-northeast-1"})._s3().meta.config
check("…a client built without explicit values gets exactly those (standard retry mode, total attempts incl. the first)",
      (default_client.connect_timeout, default_client.read_timeout, default_client.retries) == (3.0, 5.0, {"mode": "standard", "total_max_attempts": 2}),
      default_client.retries)
storage = app.extensions["photo_feature"].storage
cfg = storage._s3().meta.config
check("the app's storage client uses the configured values", (cfg.connect_timeout, cfg.read_timeout, cfg.retries["total_max_attempts"]) == (CONNECT, READ, ATTEMPTS))
client_id = id(storage._s3())

saved = os.environ.get("PHOTO_OBJECT_READ_TIMEOUT")
os.environ["PHOTO_OBJECT_READ_TIMEOUT"] = "abc"
bad = app_config._number("PHOTO_OBJECT_READ_TIMEOUT", 5.0, float)
os.environ["PHOTO_OBJECT_READ_TIMEOUT"] = " 7.5 "
good = app_config._number("PHOTO_OBJECT_READ_TIMEOUT", 5.0, float)
os.environ.pop("PHOTO_OBJECT_READ_TIMEOUT")
if saved is not None:
    os.environ["PHOTO_OBJECT_READ_TIMEOUT"] = saved
check("environment value parsing: '7.5' → 7.5; 'abc' → None (refused below, the app still starts)", good == 7.5 and bad is None)


def status_for(**over):
    fake = SimpleNamespace(config={"CONFIG_NAME": "staging", "PHOTO_UPLOADS_ENABLED": True,
                                   "PHOTO_CONSENT_APPROVED_VERSION": version, **OBJECT, **over},
                           testing=False, instance_path=".", extensions={})
    init_photos(fake)
    return fake.extensions["photo_feature"]


check("staging + valid object settings → on", status_for().upload_enabled)
unsafe = {"connect 0": dict(PHOTO_OBJECT_CONNECT_TIMEOUT=0), "connect 11 s": dict(PHOTO_OBJECT_CONNECT_TIMEOUT=11.0),
          "read 0": dict(PHOTO_OBJECT_READ_TIMEOUT=0), "read 16 s": dict(PHOTO_OBJECT_READ_TIMEOUT=16.0),
          "attempts 0": dict(PHOTO_OBJECT_MAX_ATTEMPTS=0), "attempts 4": dict(PHOTO_OBJECT_MAX_ATTEMPTS=4),
          "not a number (None)": dict(PHOTO_OBJECT_READ_TIMEOUT=None), "attempts '2' (text)": dict(PHOTO_OBJECT_MAX_ATTEMPTS="2"),
          "3 × (3 + 5) s = 24 s > 20 s": dict(PHOTO_OBJECT_CONNECT_TIMEOUT=3.0, PHOTO_OBJECT_READ_TIMEOUT=5.0, PHOTO_OBJECT_MAX_ATTEMPTS=3),
          "malformed endpoint URL": dict(PHOTO_OBJECT_ENDPOINT_URL="not a url")}
for label, over in unsafe.items():
    f = status_for(**over)
    check(f"unsafe client setting ({label}) → photos off (object_storage_not_configured), no crash",
          not f.upload_enabled and not f.view_enabled and f.reason == "object_storage_not_configured", f.reason)

# ================================================================ normal upload through the fake endpoint
S3.reset()
r, _ = upload(pt)
puts = [h for m, _k, h in S3.requests if m == "PUT"]
check("normal upload → 201, two objects written", r.status_code == 201 and len(puts) == 2 and len(S3.objects) == 2, r.status_code)
check("…each PUT asks for SSE-KMS with the configured key and Cache-Control private, no-store",
      all(h.get("x-amz-server-side-encryption") == "aws:kms" and h.get("x-amz-server-side-encryption-aws-kms-key-id") == KMS_ARN
          and h.get("cache-control") == "private, no-store" for h in puts))
attached_id = r.get_json()["data"]["id"]
rr = c.post("/api/v1/symptoms/records", json={"patient_id": "me", "form_code": "rt_skin_self_assessment",
                                              "values": [{"definition_code": "skin_itch", "option_code": "yes"}],
                                              "photos": [{"id": attached_id}]}, headers=H(pt))
check("…attached to a report (201)", rr.status_code == 201)

# ================================================================ upload failures: 503, no incomplete record
for label, modes, expect_puts in [
    ("S3 does not answer (read timeout)", {"PUT": "hang"}, ATTEMPTS),
    ("KMS denies kms:GenerateDataKey (403 AccessDenied)", {"PUT": "kms_denied"}, 1),
    ("KMS key disabled (400 KMS.DisabledException)", {"PUT": "kms_disabled"}, 1),
    ("bucket missing (404 NoSuchBucket)", {"PUT": "no_bucket"}, 1),
    ("S3 internal error (500)", {"PUT": "internal"}, ATTEMPTS),
]:
    S3.reset(**modes)
    before_rows, before_objects = rows(), len(S3.objects)
    r, elapsed = upload(pt)
    check(f"upload, {label} → 503 PHOTO_STORAGE_UNAVAILABLE in {elapsed:.1f} s (bound {BOUND:.1f} s)",
          r.status_code == 503 and code(r) == "PHOTO_STORAGE_UNAVAILABLE" and elapsed < BOUND, (r.status_code, code(r)))
    check(f"…{S3.count('PUT')} request(s) to S3 ({'retried once' if expect_puts > 1 else 'not retried: retrying cannot help'}); "
          "no photo row, no object left", S3.count("PUT") == expect_puts and rows() == before_rows and len(S3.objects) == before_objects,
          (S3.count("PUT"), rows() - before_rows, len(S3.objects) - before_objects))

S3.reset()
r, elapsed = upload(pt)  # ok again
check("storage back → the next upload (new Idempotency-Key) → 201", r.status_code == 201)

reinit(PHOTO_OBJECT_ENDPOINT_URL=f"http://127.0.0.1:{closed_port()}")
before = rows()
r, elapsed = upload(pt)
check(f"upload, nothing listening (connection refused / connect timeout) → 503 in {elapsed:.1f} s, no row",
      r.status_code == 503 and code(r) == "PHOTO_STORAGE_UNAVAILABLE" and elapsed < BOUND and rows() == before)
reinit()

# half-way: the full image is stored, the thumbnail fails → compensation removes the full image
S3.reset(PUT=lambda key: "internal" if key.endswith("thumb.jpg") else "ok")
before_rows, before_objects = rows(), len(S3.objects)
r, elapsed = upload(pt)
full_key = next(k for m, k, _ in S3.requests if m == "PUT" and k.endswith("full.jpg"))
check("half-way failure (thumbnail write fails) → 503, no row", r.status_code == 503 and rows() == before_rows)
check("…compensation: the stored full image is deleted again (DELETE sent, nothing left)",
      ("DELETE", full_key) in [(m, k) for m, k, _ in S3.requests] and len(S3.objects) == before_objects)

S3.reset(PUT=lambda key: "internal" if key.endswith("thumb.jpg") else "ok", DELETE="internal")
before_rows, before_objects = rows(), len(S3.objects)
r, elapsed = upload(pt)
check(f"half-way failure and the compensating delete fails too → 503 in {elapsed:.1f} s, still no row (no incomplete record)",
      r.status_code == 503 and rows() == before_rows and elapsed < 2 * BOUND)
print(f"[INFO] …{len(S3.objects) - before_objects} object without a row left in the bucket (only possible when S3 fails "
      "mid-compensation; found by a bucket inventory, docs §7)")

# idempotency (current behaviour, unchanged here): a 503 is stored as the key's final answer and replayed — never
# half-applied. NOTE: SkinPhotoPicker.vue retries a 5xx with the SAME key, so its retry keeps getting this 503
# until the photo is removed and added again (reported; fixing it touches the frontend or app/core/idempotency.py).
S3.reset(PUT="kms_denied")
key, same = str(uuid.uuid4()), jpeg()
r1, _ = upload(pt, key=key, data=same)
S3.reset()
r2, _ = upload(pt, key=key, data=same)
with app.app_context():
    rec = db.session.query(IdempotencyRecord).filter_by(idempotency_key=key).one()
check("same Idempotency-Key after a 503 → the failure is replayed (no second write, no row) — current behaviour",
      r1.status_code == 503 and r2.status_code == 503 and S3.count("PUT") == 0 and rec.resource_id is None)

# ================================================================ read failures
S3.reset()
r, _ = content(pt, attached_id)
check("read, normal → 200", r.status_code == 200)
for label, mode, expect_gets in [
    ("KMS denies kms:Decrypt (403 AccessDenied)", "kms_decrypt_denied", 1),
    ("S3 does not answer (read timeout)", "hang", ATTEMPTS),
    ("S3 internal error (500)", "internal", ATTEMPTS),
    ("bucket missing (404 NoSuchBucket)", "no_bucket", 1),
]:
    S3.reset(GET=mode)
    r, elapsed = content(pt, attached_id)
    check(f"read, {label} → 503 PHOTO_STORAGE_UNAVAILABLE (not 'file missing') in {elapsed:.1f} s; {S3.count('GET')} request(s)",
          r.status_code == 503 and code(r) == "PHOTO_STORAGE_UNAVAILABLE" and elapsed < BOUND and S3.count("GET") == expect_gets,
          (r.status_code, code(r), S3.count("GET")))
S3.reset()
saved_obj = {k: v for k, v in S3.objects.items()}
with app.app_context():
    attached_key = db.session.query(SymptomPhoto).filter_by(public_id=attached_id).one().storage_key
S3.objects.pop(attached_key)
r, _ = content(pt, attached_id)
check("read, object gone (NoSuchKey) → 404 PHOTO_FILE_MISSING (an integrity problem, reported as such)", r.status_code == 404 and code(r) == "PHOTO_FILE_MISSING")
S3.objects = saved_obj

# ================================================================ clean-up during an outage
S3.reset()
expired = [upload(pt)[0].get_json()["data"]["id"] for _ in range(5)]
with app.app_context():
    for p in db.session.query(SymptomPhoto).filter(SymptomPhoto.public_id.in_(expired)):
        p.created_at = utcnow() - timedelta(hours=25)
    db.session.commit()
S3.reset(DELETE="hang")
r, elapsed = upload(pt)  # the upload runs the opportunistic clean-up first
check(f"5 expired pending photos, deletes hang: the upload still answers 201 in {elapsed:.1f} s (bound {BOUND:.1f} s)",
      r.status_code == 201 and elapsed < BOUND, r.status_code)
check(f"…the clean-up stopped at the first failing delete ({S3.count('DELETE')} DELETE requests, not 10 × {ATTEMPTS})",
      S3.count("DELETE") == ATTEMPTS)
with app.app_context():
    statuses = {p.status for p in db.session.query(SymptomPhoto).filter(SymptomPhoto.public_id.in_(expired))}
    expired_keys = [k for p in db.session.query(SymptomPhoto).filter(SymptomPhoto.public_id.in_(expired)) for k in photo_services.photo_keys(p)]
check("…all 5 rows discarded anyway (state committed before any file removal)", statuses == {"discarded"})
check("…their objects are still stored (leftovers)", all(k in S3.objects for k in expired_keys))
S3.reset()
with app.app_context():
    report = photo_services.verify_storage(fix=True)
check("storage back: `photos verify --fix` finds the 10 leftovers and removes them", report["leftovers"] == 10 and not any(k in S3.objects for k in expired_keys),
      report["leftovers"])

S3.reset()
pending = upload(pt)[0].get_json()["data"]["id"]
S3.reset(DELETE="internal")
r = c.delete(f"/api/v1/symptom-photos/{pending}", headers=H(pt))
with app.app_context():
    st = db.session.query(SymptomPhoto).filter_by(public_id=pending).one().status
check("patient removes a pending photo while deletes fail → 200, row discarded (file left for verify --fix)", r.status_code == 200 and st == "discarded")

# ================================================================ nothing leaks
check("one client per storage instance, built at start-up and shared by every request of the run",
      id(storage._s3()) == client_id and storage._s3() is storage._client)
all_errors = "\n".join(responses)
check(f"{len(responses)} error responses: no bucket, account, KMS key, access / secret key, object key or patient data",
      not leaks(all_errors), leaks(all_errors))
storage_logs = [ln for ln in log_lines if "photo storage" in ln]
check("storage failures are logged with a safe code (AccessDenied, KMS.DisabledException, NoSuchBucket, InternalError, ReadTimeoutError)",
      all(any(code_ in ln for ln in storage_logs) for code_ in ("AccessDenied", "KMS.DisabledException", "NoSuchBucket", "InternalError", "ReadTimeoutError")),
      sorted({ln.split("(")[1].split(")")[0].split(";")[0] for ln in storage_logs if "(" in ln}))
log_text = "\n".join(log_lines)
check("no log line contains the bucket, KMS key, account, credentials or an object key",
      not [w for w in (BUCKET, KMS_ARN, ACCOUNT, ACCESS_KEY, SECRET_KEY, "photos/") if w in log_text])

server.shutdown()
print(f"\n{sum(ok)}/{len(ok)} checks passed")
sys.exit(0 if all(ok) else 1)
