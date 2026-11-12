"""皮膚照片 private object storage (PrivateObjectStorage, S3 + SSE-KMS) — end-to-end check through the real
boto3 client, with every phase in its OWN process (a new process = a redeploy: nothing survives but the
database and the bucket).

    python tests/object_storage/photo_object_storage_check.py          # from backend/, target: moto

TARGET moto (default) — **SIMULATED**: a local moto S3 + KMS server (127.0.0.1) and an in-memory PostgreSQL
(PGlite). moto accepts any credentials, does not enforce IAM / bucket policies / public access blocks and
does not really encrypt: a pass here proves the application's behaviour against the S3 API, **not** that
an AWS bucket, key or IAM policy is configured correctly. That needs TARGET=aws (see
docs/skin-photo-deployment.md §6) — never run in this repository so far.

TARGET aws — an isolated, NON-production test bucket you created; the PHOTO_OBJECT_* settings come from the
environment and PHOTO_CHECK_AWS_CONFIRM=isolated-test-bucket is required. No AWS resource is created.
The database is always the local in-memory PGlite — never a deployed database. Synthetic images only.

Needs: boto3 (backend/requirements.txt); for moto, `pip install "moto[server,s3,kms]==5.2.4"` in the local
venv only (test tool — not in any requirements file); node + frontend/node_modules (PGlite).
"""
import hashlib
import io
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
PHASE = os.environ.get("PHOTO_CHECK_PHASE")
TARGET = os.environ.get("PHOTO_CHECK_TARGET", "moto")
PASSWORD = "Predeploy123"  # synthetic demo accounts only
STATE = os.environ.get("PHOTO_CHECK_STATE")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""), flush=True)


def s3_client():
    import boto3

    return boto3.client("s3", region_name=os.environ["PHOTO_OBJECT_REGION"], endpoint_url=os.environ.get("PHOTO_OBJECT_ENDPOINT_URL") or None,
                        aws_access_key_id=os.environ["PHOTO_OBJECT_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["PHOTO_OBJECT_SECRET_ACCESS_KEY"])


def object_missing(s3, key):
    try:
        s3.head_object(Bucket=os.environ["PHOTO_OBJECT_BUCKET"], Key=key)
        return False
    except Exception as exc:  # noqa: BLE001
        return getattr(exc, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound")


# =============================================================================== child phases (one process each)
def child():
    sys.path.insert(0, str(BACKEND))
    from PIL import Image
    from werkzeug.security import generate_password_hash

    from app import create_app
    from app.extensions import db
    from app.models import NursePatientAssignment, PatientProfile, Role, SymptomPhoto, User
    from app.models.base import utcnow

    if PHASE == "gate":
        if os.environ.get("PHOTO_CHECK_NO_BOTO3"):
            sys.modules["boto3"] = None
        app = create_app(os.environ["FLASK_ENV"])
        f = app.extensions["photo_feature"]
        print("GATE " + json.dumps({"upload": f.upload_enabled, "view": f.view_enabled, "reason": f.reason,
                                    "storage": type(f.storage).__name__ if f.storage else None}))
        return 0

    app = create_app(os.environ["FLASK_ENV"])
    c = app.test_client()
    bucket = os.environ["PHOTO_OBJECT_BUCKET"]
    secret_values = [os.environ["PHOTO_OBJECT_ACCESS_KEY_ID"], os.environ["PHOTO_OBJECT_SECRET_ACCESS_KEY"]]
    endpoint_host = (os.environ.get("PHOTO_OBJECT_ENDPOINT_URL") or "").split("//")[-1]

    def orm(fn):  # PGlite takes one connection: never hold a session while a request runs
        with app.app_context():
            try:
                return fn()
            finally:
                db.session.remove()

    def token(email):
        r = c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        return r.get_json()["data"]["access_token"]

    def H(t):
        return {"Authorization": f"Bearer {t}", "Idempotency-Key": str(uuid.uuid4())}

    def code(r):
        return (r.get_json(silent=True) or {}).get("error", {}).get("code")

    def jpeg(color, size=(2400, 1800), gps=False):
        buf = io.BytesIO()
        kw = {}
        if gps:
            ex = Image.Exif()
            ex[0x010F], ex[0x0110] = "TestMaker", "TestPhone-X"
            ex[0x8825] = {1: "N", 2: (25.0, 2.0, 0.0), 3: "E", 4: (121.0, 33.0, 0.0)}
            kw["exif"] = ex
        Image.new("RGB", size, color).save(buf, "JPEG", quality=90, **kw)
        return buf.getvalue()

    def upload(t, data, name="IMG_20261112_skin.jpg"):
        r = c.post("/api/v1/patients/me/symptom-photos", data={"file": (io.BytesIO(data), name, "image/jpeg")},
                   headers=H(t), content_type="multipart/form-data")
        return r, (r.get_json(silent=True) or {})

    def content(t, photo_id, variant="full"):
        return c.get(f"/api/v1/symptom-photos/{photo_id}/content?variant={variant}", headers={"Authorization": f"Bearer {t}"} if t else {})

    def leaks(text):
        """Anything in an API response that would point at the bucket or reveal a credential."""
        found = [w for w in [bucket, endpoint_host, "amazonaws", "X-Amz-", "photos/", *secret_values] if w and w in text]
        return found

    def row(public_id):
        return orm(lambda: db.session.query(SymptomPhoto).filter_by(public_id=public_id).one())

    state = json.loads(Path(STATE).read_text(encoding="utf-8")) if STATE and Path(STATE).exists() else {}

    def save():
        Path(STATE).write_text(json.dumps(state), encoding="utf-8")

    # ------------------------------------------------------------------------------------------- seed
    if PHASE == "seed":
        from app.seeds.dev import seed_dev_data

        def seed():
            seed_dev_data(password=PASSWORD)
            roles = {r.name: r for r in db.session.query(Role)}
            pw = generate_password_hash(PASSWORD)
            nurse2 = User(role=roles["nurse"], email="nurse02@demo.local", password_hash=pw, display_name="測試護理師 陳", password_changed_at=utcnow())
            u2 = User(role=roles["patient"], email="p2@demo.local", password_hash=pw, display_name="乙", password_changed_at=utcnow())
            from datetime import date
            p2 = PatientProfile(patient_code="P09002", display_name="測試病人 乙", date_of_birth=date(1960, 1, 1), user=u2)
            db.session.add_all([nurse2, p2])
            db.session.flush()
            db.session.add(NursePatientAssignment(nurse_id=nurse2.id, patient_id=p2.id))
            db.session.commit()
            return db.session.query(PatientProfile).filter_by(patient_code="P00001").one().public_id
        state["patient"] = orm(seed)
        save()
        print("seeded synthetic accounts")
        return 0

    pt, nt = token("patient01@demo.local"), token("nurse01@demo.local")
    s3 = s3_client()

    # ------------------------------------------------------------------------------------------- upload
    if PHASE == "upload":
        s = c.get("/api/v1/symptom-photos/settings", headers=H(pt)).get_json()["data"]
        check("settings: uploads ON with the object backend (staging config, all settings present)", s["upload_enabled"] and s["view_enabled"], s.get("reason"))
        r = c.post("/api/v1/patients/me/photo-consent", json={"version": s["consent"]["version"]}, headers=H(pt))
        check("patient accepts the consent version shown", r.status_code == 201)

        a_src = jpeg((200, 120, 110), gps=True)
        ra, a = upload(pt, a_src)
        rb, b = upload(pt, jpeg((190, 110, 100)))
        rc, cc = upload(pt, jpeg((180, 100, 90)))
        check("upload 3 synthetic photos → 201 pending", (ra.status_code, rb.status_code, rc.status_code) == (201, 201, 201),
              (ra.status_code, rb.status_code, rc.status_code))
        texts = ra.get_data(as_text=True) + rb.get_data(as_text=True) + rc.get_data(as_text=True)
        check("upload responses reveal no bucket / endpoint / storage key / credential", not leaks(texts), leaks(texts))
        A, B, C = a["data"]["id"], b["data"]["id"], cc["data"]["id"]
        rows = {k: row(v) for k, v in (("A", A), ("B", B), ("C", C))}
        keys = {k: [r_.storage_key, r_.thumb_storage_key] for k, r_ in rows.items()}
        check("rows record the object backend", all(r_.storage_backend == "object" for r_ in rows.values()))
        pattern = re.compile(r"^photos/[0-9a-f]{32}/(full|thumb)\.jpg$")
        flat = [k for ks in keys.values() for k in ks]
        check("object keys are random (photos/<32 hex>/full|thumb.jpg): no patient id, code, e-mail or file name",
              all(pattern.match(k) for k in flat) and not any(x in k for k in flat for x in (state["patient"], "P00001", "patient01", "IMG_")))

        heads = [s3.head_object(Bucket=bucket, Key=k) for k in keys["A"]]
        kms_id = os.environ["PHOTO_OBJECT_KMS_KEY_ID"]
        check("each object: SSE-KMS with the configured key", all(h.get("ServerSideEncryption") == "aws:kms" and kms_id in (h.get("SSEKMSKeyId") or "") for h in heads),
              [(h.get("ServerSideEncryption"), h.get("SSEKMSKeyId")) for h in heads])
        check("each object: Cache-Control private, no-store; image/jpeg", all(h.get("CacheControl") == "private, no-store" and h.get("ContentType") == "image/jpeg" for h in heads))
        grants = [g_ for k in keys["A"] for g_ in s3.get_object_acl(Bucket=bucket, Key=k)["Grants"]]
        public = [g_ for g_ in grants if "AllUsers" in str(g_.get("Grantee", {}).get("URI", "")) or "AuthenticatedUsers" in str(g_.get("Grantee", {}).get("URI", ""))]
        check("object ACLs grant nothing to AllUsers / AuthenticatedUsers (private)", grants and not public, public)
        stored = s3.get_object(Bucket=bucket, Key=keys["A"][0])["Body"].read()
        im = Image.open(io.BytesIO(stored))
        check("stored bytes = the re-encoded image (checksum in the row), ≤ 1600 px, no EXIF / GPS",
              hashlib.sha256(stored).hexdigest() == rows["A"].checksum_sha256 and max(im.size) <= 1600 and len(im.getexif()) == 0
              and b"TestPhone-X" not in stored and stored != a_src, im.size)

        rd = c.delete(f"/api/v1/symptom-photos/{C}", headers=H(pt))
        check("DELETE a pending photo (removed before submit) → 200, both objects deleted from the bucket",
              rd.status_code == 200 and all(object_missing(s3, k) for k in keys["C"]))
        versions = s3.list_object_versions(Bucket=bucket, Prefix=keys["C"][0])
        kept = [v for v in versions.get("Versions", []) if v["Key"] == keys["C"][0]]
        markers = [m for m in versions.get("DeleteMarkers", []) if m["Key"] == keys["C"][0]]
        if os.environ.get("PHOTO_CHECK_VERSIONING") == "on":
            check("versioning: the app's delete adds a delete marker (latest); the bytes remain as a non-current version",
                  len(markers) == 1 and markers[0]["IsLatest"] and len(kept) == 1 and not kept[0]["IsLatest"])
            old = s3.get_object(Bucket=bucket, Key=keys["C"][0], VersionId=kept[0]["VersionId"])["Body"].read()
            check("…so a 'removed before submit' photo is still recoverable by version id until a lifecycle rule expires it "
                  "(the app itself never reads versions)", len(old) > 0)
            print("[INFO] the app has no s3:DeleteObjectVersion call (and should not get that permission): permanent "
                  "removal of non-current versions is the bucket lifecycle rule's job (docs §7)", flush=True)

        body = {"patient_id": "me", "form_code": "rt_skin_self_assessment", "values": [{"definition_code": "skin_itch", "option_code": "yes"}],
                "photos": [{"id": A, "body_site": "head_neck"}, {"id": B}]}
        rr = c.post("/api/v1/symptoms/records", json=body, headers=H(pt))
        check("report with 2 photos → 201 (attached)", rr.status_code == 201, rr.status_code)

        rn = content(nt, A)
        check("assigned nurse reads the full image → 200, bytes = the bucket object", rn.status_code == 200 and rn.data == stored)
        check("image response headers: no-store, nosniff, inline photo.jpg, no redirect to the bucket",
              "no-store" in rn.headers.get("Cache-Control", "") and rn.headers.get("X-Content-Type-Options") == "nosniff"
              and rn.headers.get("Content-Disposition") == 'inline; filename="photo.jpg"' and "Location" not in rn.headers)
        rh = c.get(f"/api/v1/patients/{state['patient']}/symptom-photos", headers=H(nt))
        ids = [i["id"] for i in rh.get_json()["data"]]
        check("nurse history lists both photos", rh.status_code == 200 and {A, B} <= set(ids))
        check("history response reveals no bucket / endpoint / storage key / credential / presigned URL",
              not leaks(rh.get_data(as_text=True)), leaks(rh.get_data(as_text=True)))
        state.update(A=A, B=B, C=C, keys=keys, sha_a=rows["A"].checksum_sha256, sha_b=rows["B"].checksum_sha256)
        save()

    # ------------------------------------------------------------------------------------------- after a restart
    elif PHASE == "restart":
        A, B, C = state["A"], state["B"], state["C"]
        f = app.extensions["photo_feature"]
        check("new process: photo feature initialised again from the environment (object backend)", f.upload_enabled and type(f.storage).__name__ == "PrivateObjectStorage")
        ra, rat, rb = content(nt, A), content(nt, A, "thumb"), content(nt, B)
        check("new process: nurse reads A (full + thumb) and B → 200", (ra.status_code, rat.status_code, rb.status_code) == (200, 200, 200))
        check("new process: bytes match the checksums recorded at upload",
              hashlib.sha256(ra.data).hexdigest() == state["sha_a"] and hashlib.sha256(rb.data).hexdigest() == state["sha_b"])
        check("new process: patient reads own photo → 200", content(pt, A).status_code == 200)
        p2t, nt2, at = token("p2@demo.local"), token("nurse02@demo.local"), token("admin01@demo.local")
        check("another patient → 404", content(p2t, A).status_code == 404)
        check("a nurse not assigned to the patient → 404", content(nt2, A).status_code == 404)
        check("admin → 403", content(at, A).status_code == 403)
        check("no token → 401", content(None, A).status_code == 401)
        check("another patient / unassigned nurse cannot list the patient's photos",
              c.get(f"/api/v1/patients/{state['patient']}/symptom-photos", headers=H(p2t)).status_code in (403, 404)
              and c.get(f"/api/v1/patients/{state['patient']}/symptom-photos", headers=H(nt2)).status_code in (403, 404))
        check("the photo removed before submit → 404", content(pt, C).status_code == 404)

    # ------------------------------------------------------------------------------------------- deletion + failures
    elif PHASE == "failures":
        A, B = state["A"], state["B"]
        s3.delete_object(Bucket=bucket, Key=state["keys"]["B"][0])
        rb = content(nt, B)
        check("object deleted outside the app → 404 PHOTO_FILE_MISSING (not 500, not 'no access')", rb.status_code == 404 and code(rb) == "PHOTO_FILE_MISSING")

        rd, d = upload(pt, jpeg((170, 90, 80)))
        D = d["data"]["id"]
        d_keys = [row(D).storage_key, row(D).thumb_storage_key]

        def age():
            from datetime import timedelta
            p = db.session.query(SymptomPhoto).filter_by(public_id=D).one()
            p.created_at = utcnow() - timedelta(hours=25)
            db.session.commit()
        orm(age)
        state.update(D=D, d_keys=d_keys)
        save()

        def end(active):
            p1 = db.session.query(PatientProfile).filter_by(public_id=state["patient"]).one()
            a_ = db.session.query(NursePatientAssignment).filter(NursePatientAssignment.patient_id == p1.id,
                                                                 NursePatientAssignment.nurse.has(email="nurse01@demo.local")).first()
            a_.ended_at = None if active else utcnow()
            db.session.commit()
        orm(lambda: end(False))
        check("assignment ended → the nurse gets 404 at once", content(nt, A).status_code == 404)
        orm(lambda: end(True))
        check("assignment restored → 200 again", content(nt, A).status_code == 200)

    elif PHASE == "after-purge":
        p = row(state["D"])
        check("`flask photos purge-pending`: the 25-hour-old pending photo discarded (row kept) and both objects deleted",
              p.status == "discarded" and p.delete_reason == "expired_unattached" and all(object_missing(s3, k) for k in state["d_keys"]))
        check("…the attached photo A is untouched", not object_missing(s3, state["keys"]["A"][0]))

    elif PHASE == "storage-down":  # PHOTO_OBJECT_ENDPOINT_URL / PHOTO_OBJECT_BUCKET broken by the caller
        before = orm(lambda: db.session.query(SymptomPhoto).count())
        r, _ = upload(pt, jpeg((160, 80, 70)))
        after = orm(lambda: db.session.query(SymptomPhoto).count())
        label = os.environ["PHOTO_CHECK_LABEL"]
        check(f"{label}: upload → 503 PHOTO_STORAGE_UNAVAILABLE, no row added", r.status_code == 503 and code(r) == "PHOTO_STORAGE_UNAVAILABLE" and after == before,
              (r.status_code, code(r), before, after))
        check(f"{label}: …the error response reveals no bucket / endpoint / credential", not leaks(r.get_data(as_text=True)))
        rr = content(nt, state["A"])
        check(f"{label}: read → 503 PHOTO_STORAGE_UNAVAILABLE (not reported as a missing file)", rr.status_code == 503 and code(rr) == "PHOTO_STORAGE_UNAVAILABLE",
              (rr.status_code, code(rr)))

    print(f"{sum(ok)}/{len(ok)} checks passed")
    return 0 if all(ok) else 1


# =============================================================================== orchestrator
def free_port():
    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        return s_.getsockname()[1]


def wait_port(port, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def instance_snapshot():
    root = BACKEND / "instance"
    return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()) if root.exists() else []


def main():
    banner = ("SIMULATED — moto S3 + KMS on 127.0.0.1. NOT a verification of AWS S3 / KMS / IAM." if TARGET == "moto"
              else "AWS — an isolated, non-production test bucket.")
    print(f"===== photo object storage check — target: {TARGET} ({banner})")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PHOTO_", "AWS_", "SEED_"))}
    env.update(FLASK_ENV="staging", CORS_ORIGINS="https://photo-check.invalid", PYTHONIOENCODING="utf-8",
               SECRET_KEY=secrets.token_hex(32), JWT_SECRET_KEY=secrets.token_hex(32),
               AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_EC2_METADATA_DISABLED="true")
    consent_version = re.search(r'^CONSENT_VERSION = "([^"]+)"', (BACKEND / "app/modules/photo/consent.py").read_text(encoding="utf-8"), re.M).group(1)
    procs = []
    tmp = tempfile.mkdtemp(prefix="photo-check-")
    if TARGET == "moto":
        import logging

        from moto.server import ThreadedMotoServer

        logging.getLogger("werkzeug").setLevel(logging.ERROR)  # moto's access log would print object keys
        port = free_port()
        server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
        server.start()
        region = "ap-northeast-1"  # placeholder: the hospital has not chosen a region
        env.update(PHOTO_OBJECT_ENDPOINT_URL=f"http://127.0.0.1:{port}", PHOTO_OBJECT_REGION=region,
                   PHOTO_OBJECT_BUCKET=f"ccp-photo-check-{secrets.token_hex(4)}",
                   PHOTO_OBJECT_ACCESS_KEY_ID="moto-test-" + secrets.token_hex(6), PHOTO_OBJECT_SECRET_ACCESS_KEY=secrets.token_hex(20))
        import boto3

        kms = boto3.client("kms", region_name=region, endpoint_url=env["PHOTO_OBJECT_ENDPOINT_URL"],
                           aws_access_key_id=env["PHOTO_OBJECT_ACCESS_KEY_ID"], aws_secret_access_key=env["PHOTO_OBJECT_SECRET_ACCESS_KEY"])
        env["PHOTO_OBJECT_KMS_KEY_ID"] = kms.create_key(Description="photo check (moto)")["KeyMetadata"]["KeyId"]
        os.environ.update({k: v for k, v in env.items() if k.startswith("PHOTO_OBJECT_")})
        s3 = s3_client()
        bucket = env["PHOTO_OBJECT_BUCKET"]
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region})
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
        s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={"Rules": [{
            "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "aws:kms", "KMSMasterKeyID": env["PHOTO_OBJECT_KMS_KEY_ID"]}, "BucketKeyEnabled": True}]})
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        env["PHOTO_CHECK_VERSIONING"] = "on"
        print("moto: KMS key, private bucket (public access block, default SSE-KMS, versioning) created in memory")
        # the lifecycle rule recommended in docs §7 — shape only: <N> days is a placeholder, NOT a retention decision
        rule = {"ID": "photos-noncurrent-versions", "Status": "Enabled", "Filter": {"Prefix": "photos/"},
                "NoncurrentVersionExpiration": {"NoncurrentDays": 30}, "Expiration": {"ExpiredObjectDeleteMarker": True},
                "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1}}
        s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={"Rules": [rule]})
        back = s3.get_bucket_lifecycle_configuration(Bucket=bucket)["Rules"][0]
        lifecycle_ok = bool(back.get("NoncurrentVersionExpiration") and back["Expiration"].get("ExpiredObjectDeleteMarker")
                            and "Days" not in back["Expiration"])
    else:
        need = ["PHOTO_OBJECT_BUCKET", "PHOTO_OBJECT_REGION", "PHOTO_OBJECT_KMS_KEY_ID", "PHOTO_OBJECT_ACCESS_KEY_ID", "PHOTO_OBJECT_SECRET_ACCESS_KEY"]
        missing = [n for n in need if not os.environ.get(n)]
        bucket = os.environ.get("PHOTO_OBJECT_BUCKET", "")
        if missing or os.environ.get("PHOTO_CHECK_AWS_CONFIRM") != "isolated-test-bucket" or re.search(r"prod|prd", bucket, re.I):
            print("refusing: set " + ", ".join(need) + " for an isolated NON-production test bucket and PHOTO_CHECK_AWS_CONFIRM=isolated-test-bucket")
            return 2
        env.update({n: os.environ[n] for n in need + ["PHOTO_OBJECT_ENDPOINT_URL"] if os.environ.get(n)})
        s3 = s3_client()

    env.update(PHOTO_UPLOADS_ENABLED="true", PHOTO_STORAGE_BACKEND="object", PHOTO_CONSENT_APPROVED_VERSION=consent_version)
    pg_port = free_port()
    pglite = REPO / "frontend/node_modules/@electric-sql/pglite-socket/dist/scripts/server.js"
    procs.append(subprocess.Popen(["node", str(pglite), "--db=memory://", f"--port={pg_port}", "--host=127.0.0.1"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    if not wait_port(pg_port):
        print("PGlite did not start")
        return 2
    env["DATABASE_URL"] = f"postgresql://postgres:postgres@127.0.0.1:{pg_port}/postgres"
    env["PHOTO_CHECK_STATE"] = str(Path(tmp) / "state.json")
    outputs, results = [], []
    snap_before = instance_snapshot()

    def run(args, **extra):
        e = {**env, **extra}
        p = subprocess.run(args, cwd=BACKEND, env=e, capture_output=True, text=True, encoding="utf-8", timeout=600)
        outputs.append(p.stdout + p.stderr)
        return p

    def phase(name, **extra):
        p = run([sys.executable, __file__], PHOTO_CHECK_PHASE=name, PHOTO_CHECK_TARGET=TARGET, **extra)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith(("[PASS]", "[FAIL]", "[INFO]"))]
        print("\n".join(lines))
        results.extend(ln.startswith("[PASS]") for ln in lines if not ln.startswith("[INFO]"))
        if p.returncode != 0 and not any(ln.startswith("[FAIL]") for ln in lines):
            print(f"[FAIL] phase {name} crashed (exit {p.returncode})\n{p.stderr[-2500:]}")
            results.append(False)
        return p

    def expect(label, cond, detail=""):
        results.append(bool(cond))
        print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))

    try:
        if TARGET == "moto":
            expect("lifecycle rule shape accepted (non-current versions expire; expired delete markers removed; current "
                   "objects never expire) — syntax only: moto does not run lifecycle rules; the day count is a placeholder", lifecycle_ok)
        print("----- 1. switch: uploads are on only with every setting (each variant = a fresh process)")

        def gate(**extra):
            p = run([sys.executable, __file__], PHOTO_CHECK_PHASE="gate", **extra)
            m = re.search(r"^GATE (.*)$", p.stdout, re.M)
            return json.loads(m.group(1)) if m else {"error": p.stderr[-400:]}
        g = gate()
        expect("staging + object backend + all settings + approved consent version → uploads ON", g.get("upload") and g.get("storage") == "PrivateObjectStorage", g)
        prod_env = {"FLASK_ENV": "production", "PHOTO_UPLOADS_ENABLED": "", "PHOTO_STORAGE_BACKEND": "", "PHOTO_CONSENT_APPROVED_VERSION": ""}
        g = gate(**prod_env)
        expect("production with no photo settings (today's Render) → OFF, no storage", g.get("upload") is False and g.get("view") is False, g)
        g = gate(PHOTO_UPLOADS_ENABLED="false")
        expect("PHOTO_UPLOADS_ENABLED=false → OFF (disabled_by_config)", g.get("upload") is False and g.get("reason") == "disabled_by_config", g)
        for name in ("PHOTO_OBJECT_BUCKET", "PHOTO_OBJECT_REGION", "PHOTO_OBJECT_KMS_KEY_ID", "PHOTO_OBJECT_ACCESS_KEY_ID", "PHOTO_OBJECT_SECRET_ACCESS_KEY"):
            g = gate(**{name: ""})
            expect(f"{name} missing → OFF, no storage (object_storage_not_configured)", g.get("upload") is False and g.get("view") is False
                   and g.get("reason") == "object_storage_not_configured", g.get("reason"))
        g = gate(PHOTO_CHECK_NO_BOTO3="1")
        expect("boto3 not importable → OFF (object_storage_not_configured)", g.get("upload") is False and g.get("reason") == "object_storage_not_configured", g)
        g = gate(PHOTO_CONSENT_APPROVED_VERSION="some-other-version")
        expect("consent version not approved → uploads OFF, existing photos still readable", g.get("upload") is False and g.get("view") is True
               and g.get("reason") == "consent_not_approved", g)
        g = gate(PHOTO_STORAGE_BACKEND="local")
        expect("local disk in staging → refused (local_storage_not_allowed)", g.get("upload") is False and g.get("reason") == "local_storage_not_allowed", g)

        print("----- 2. database (in-memory PostgreSQL) + synthetic accounts")
        p = run([sys.executable, "-m", "flask", "--app", "run", "db", "upgrade"])
        expect("flask db upgrade on the throw-away database", p.returncode == 0, p.stderr[-300:] if p.returncode else "")
        phase("seed")

        print("----- 3. process 1: consent, upload, encryption, privacy, delete before submit, submit, nurse reads")
        phase("upload")
        print("----- 4. process 2 (= after a redeploy: new process, nothing kept but the database and the bucket)")
        phase("restart")
        p = run([sys.executable, "-m", "flask", "--app", "run", "photos", "verify"])
        expect("`flask photos verify` (boto3 head_object for every row): exit 0, nothing missing", p.returncode == 0 and "0 missing" in p.stdout, p.stdout.strip()[:200])

        print("----- 5. deletion + failure handling (new processes)")
        phase("failures")
        p = run([sys.executable, "-m", "flask", "--app", "run", "photos", "verify"])
        expect("`flask photos verify` reports the object deleted outside the app: exit 1, listed", p.returncode == 1 and "missing: photo" in p.stdout, p.stdout.strip()[:200])
        p = run([sys.executable, "-m", "flask", "--app", "run", "photos", "purge-pending"])
        expect("`flask photos purge-pending` → exit 0", p.returncode == 0 and "Discarded 1" in p.stdout, p.stdout.strip()[:200])
        phase("after-purge")
        phase("storage-down", PHOTO_OBJECT_ENDPOINT_URL=f"http://127.0.0.1:{free_port()}", PHOTO_CHECK_LABEL="storage unreachable")
        phase("storage-down", PHOTO_OBJECT_BUCKET=f"ccp-photo-missing-{secrets.token_hex(4)}", PHOTO_CHECK_LABEL="bucket missing / no access")

        print("----- 6. nothing on the local disk, nothing secret in any output")
        expect("no file written or changed under backend/instance (object backend only; app.db untouched)", instance_snapshot() == snap_before)
        state = json.loads(Path(env["PHOTO_CHECK_STATE"]).read_text(encoding="utf-8"))
        blob = "\n".join(outputs)
        keys = [k for ks in state["keys"].values() for k in ks]
        expect("application output / logs contain no credential and no object key",
               env["PHOTO_OBJECT_SECRET_ACCESS_KEY"] not in blob and env["PHOTO_OBJECT_ACCESS_KEY_ID"] not in blob and not any(k in blob for k in keys))
    finally:
        for p in procs:
            p.kill()
        if TARGET == "moto":
            server.stop()
    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed — target: {TARGET}" + (" (SIMULATED; not an AWS verification)" if TARGET == "moto" else ""))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(child() if PHASE else main())
