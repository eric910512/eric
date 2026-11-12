"""皮膚照片 database storage on a REAL, throw-away PostgreSQL — never a deployed database.

    python tests/predeploy/photo_db_storage_check.py            # from backend/
    PG_BIN=<PostgreSQL bin directory> python …                  # another install, e.g. 17 like Render

Starts its own PostgreSQL server (default: the `pgserver` package's binaries — a local test tool, not in any
requirements file: `pip install pgserver==0.1.4`) in a temporary directory on 127.0.0.1, runs every phase in
its own process with the staging configuration and photos ON (PHOTO_STORAGE_BACKEND=database,
PHOTO_DB_QUOTA_BYTES=209715200 = 200 MiB from the environment), synthetic accounts and generated images only:

  1. migration to b8c3f1d7e2a6: BYTEA column, STORAGE EXTERNAL, constraints, foreign key RESTRICT
  2. upload in one process → read back in a NEW process (redeploy), access isolation; removal before submit and
     the expired clean-up delete the images in the same transaction, an attached photo is kept (409)
  3. list / history / record queries never touch symptom_photo_blobs (SQL capture on PostgreSQL)
  4. concurrent uploads against the quota: with the advisory lock no overshoot; without it (control run)
     the same race overshoots — so the test can see the problem it guards against
  5. the 200 MiB quota: filled to the limit → 507 PHOTO_STORAGE_FULL, text reports / chat / reads still work;
     `flask photos storage-report` (critical, exit 2, no patient data)
  6. pg_dump -Fc → pg_restore into a new database → images readable and identical in a new process
  7. downgrade guard: refused while images exist; an empty table downgrades and upgrades again

Default PostgreSQL here is 16 (pgserver); Render runs 17. Both pass (16.2 and, with PG_BIN, 17.11 — 2026-11-12;
tests/predeploy/rehearsal_pg.py likewise).
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
import threading
import time
import uuid
from pathlib import Path

BACKEND = Path(os.environ.get("BACKEND") or Path(__file__).resolve().parents[2])  # the release under test (phases)
PHASE = os.environ.get("PHOTO_CHECK_PHASE")
STATE = os.environ.get("PHOTO_CHECK_STATE")
PASSWORD = "Predeploy123"  # synthetic accounts only
QUOTA = 200 * 1024 * 1024
PII = ["patient01@demo.local", "p2@demo.local", "cc0", "P00001", "P09002", "P0910"]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
ok = []


def check(label, cond, detail=""):
    ok.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""), flush=True)


# =============================================================================== child phases
def child():
    sys.path.insert(0, str(BACKEND))
    from datetime import date

    from PIL import Image
    from sqlalchemy import event, text
    from werkzeug.security import generate_password_hash

    from app import create_app
    from app.extensions import db
    from app.models import NursePatientAssignment, PatientProfile, Role, SymptomPhoto, SymptomPhotoBlob, User
    from app.models.base import utcnow
    from app.modules.photo import services as photo_services
    from app.services.photo_storage import DatabasePhotoStorage, PhotoStorageFull

    app = create_app(os.environ["FLASK_ENV"])
    c = app.test_client()
    storage = app.extensions["photo_feature"].storage
    state = json.loads(Path(STATE).read_text(encoding="utf-8")) if Path(STATE).exists() else {}

    def save():
        Path(STATE).write_text(json.dumps(state), encoding="utf-8")

    def q(sql, **kw):
        with app.app_context():
            try:
                return db.session.execute(text(sql), kw).scalar()
            finally:
                db.session.remove()

    def token(email):
        return c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).get_json()["data"]["access_token"]

    def H(t):
        return {"Authorization": f"Bearer {t}", "Idempotency-Key": str(uuid.uuid4())}

    def code(r):
        return (r.get_json(silent=True) or {}).get("error", {}).get("code")

    def jpeg():
        buf = io.BytesIO()
        Image.frombytes("RGB", (60, 45), os.urandom(60 * 45 * 3)).resize((2000, 1500)).save(buf, "JPEG", quality=85)
        return buf.getvalue()

    def upload(t, data=None, client=None):
        r = (client or c).post("/api/v1/patients/me/symptom-photos", data={"file": (io.BytesIO(data or jpeg()), "IMG_0001.jpg", "image/jpeg")},
                               headers=H(t), content_type="multipart/form-data")
        return r, (r.get_json(silent=True) or {}).get("data", {})

    def content(t, pid_, variant="full"):
        return c.get(f"/api/v1/symptom-photos/{pid_}/content?variant={variant}", headers={"Authorization": f"Bearer {t}"})

    def consent(t):
        v = c.get("/api/v1/symptom-photos/settings", headers=H(t)).get_json()["data"]["consent"]["version"]
        c.post("/api/v1/patients/me/photo-consent", json={"version": v}, headers=H(t))

    def report(t, photos=None):
        body = {"patient_id": "me", "form_code": "rt_skin_self_assessment", "values": [{"definition_code": "skin_itch", "option_code": "yes"}]}
        if photos:
            body["photos"] = [{"id": p} for p in photos]
        return c.post("/api/v1/symptoms/records", json=body, headers=H(t))

    def used():
        with app.app_context():
            try:
                return storage.used_bytes()
            finally:
                db.session.remove()

    # --------------------------------------------------------------------------------------------- schema
    if PHASE == "schema":
        check("migrated to b8c3f1d7e2a6 (the new head)", q("SELECT version_num FROM alembic_version") == "b8c3f1d7e2a6")
        check("symptom_photo_blobs.data is BYTEA", q("SELECT data_type FROM information_schema.columns WHERE table_name='symptom_photo_blobs' AND column_name='data'") == "bytea")
        check("…with STORAGE EXTERNAL (no compression attempt on JPEG)",
              q("SELECT attstorage FROM pg_attribute WHERE attrelid='symptom_photo_blobs'::regclass AND attname='data'") == "e")
        names = q("SELECT string_agg(conname, ',' ORDER BY conname) FROM pg_constraint WHERE conrelid='symptom_photo_blobs'::regclass")
        want = {"ck_symptom_photo_blobs_variant", "ck_symptom_photo_blobs_content_type", "ck_symptom_photo_blobs_byte_size",
                "ck_symptom_photo_blobs_data_size", "fk_symptom_photo_blobs_photo_id", "pk_symptom_photo_blobs",
                "uq_symptom_photo_blobs_photo_id_variant", "uq_symptom_photo_blobs_storage_key"}
        check("constraints: variant, content type, size ≤ 5 MiB, length(data) = byte_size, FK, unique key / (photo, variant)",
              want <= set(names.split(",")), names)
        check("foreign key ON DELETE RESTRICT", q("SELECT confdeltype FROM pg_constraint WHERE conname='fk_symptom_photo_blobs_photo_id'") == "r")
        check("symptom_photos has no image column (metadata only, unchanged)",
              q("SELECT count(*) FROM information_schema.columns WHERE table_name='symptom_photos' AND data_type='bytea'") == 0)
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        with app.app_context():
            diff = [d for d in compare_metadata(MigrationContext.configure(db.session.connection()), db.metadata)
                    if "symptom_photo_blobs" in str(d)]
            db.session.remove()
        check("model and migration agree for symptom_photo_blobs (no schema drift)", not diff, diff[:3])

    # --------------------------------------------------------------------------------------------- seed
    elif PHASE == "seed":
        from app.seeds.dev import seed_dev_data
        with app.app_context():
            seed_dev_data(password=PASSWORD)
            roles = {r.name: r for r in db.session.query(Role)}
            pw = generate_password_hash(PASSWORD)
            nurse2 = User(role=roles["nurse"], email="nurse02@demo.local", password_hash=pw, display_name="測試護理師 陳", password_changed_at=utcnow())
            people = [("p2@demo.local", "P09002")] + [(f"cc{i:02d}@demo.local", f"P0910{i}") for i in range(1, 9)] + [("filler@demo.local", "P09199")]
            profiles = []
            for email, pcode in people:
                u = User(role=roles["patient"], email=email, password_hash=pw, display_name="合成病人", password_changed_at=utcnow())
                profiles.append(PatientProfile(patient_code=pcode, display_name="合成病人", date_of_birth=date(1960, 1, 1), user=u))
            db.session.add_all([nurse2, *profiles])
            db.session.flush()
            db.session.add(NursePatientAssignment(nurse_id=nurse2.id, patient_id=profiles[0].id))
            db.session.commit()
            state["patient"] = db.session.query(PatientProfile).filter_by(patient_code="P00001").one().public_id
            state["filler"] = [profiles[-1].id, profiles[-1].user_id]
            db.session.remove()
        save()
        print("seeded synthetic accounts")

    # --------------------------------------------------------------------------------------------- upload / restart
    elif PHASE == "upload":
        pt = token("patient01@demo.local")
        consent(pt)
        ra, a = upload(pt)
        rb, b = upload(pt)
        check("process 1: two uploads → 201 (images stored in PostgreSQL)", (ra.status_code, rb.status_code) == (201, 201))
        check("…attached to a report", report(pt, [a["id"]]).status_code == 201)
        with app.app_context():
            p = db.session.query(SymptomPhoto).filter_by(public_id=a["id"]).one()
            state.update(A=a["id"], B=b["id"], sha_a=p.checksum_sha256, size_a=p.file_size)
            db.session.remove()
        check("…2 photos × 2 images in symptom_photo_blobs", q("SELECT count(*) FROM symptom_photo_blobs") == 4)
        save()

    elif PHASE == "delete":  # removal and clean-up on PostgreSQL: status change + image delete in one transaction
        pt = token("patient01@demo.local")
        n_blobs = lambda public_id: q("SELECT count(*) FROM symptom_photo_blobs b JOIN symptom_photos p ON p.id = b.photo_id "  # noqa: E731
                                      "WHERE p.public_id = :p", p=public_id)
        r = c.delete(f"/api/v1/symptom-photos/{state['B']}", headers=H(pt))
        check("patient removes the pending photo → 200: discarded, both image rows gone",
              r.status_code == 200 and q("SELECT status FROM symptom_photos WHERE public_id = :p", p=state["B"]) == "discarded"
              and n_blobs(state["B"]) == 0)
        r = c.delete(f"/api/v1/symptom-photos/{state['A']}", headers=H(pt))
        check("an attached photo cannot be removed → 409 PHOTO_RETAINED, its images kept", r.status_code == 409 and n_blobs(state["A"]) == 2)
        _r, d = upload(pt)
        with app.app_context():
            p = db.session.query(SymptomPhoto).filter_by(public_id=d["id"]).one()
            p.created_at = utcnow() - __import__("datetime").timedelta(hours=25)
            db.session.commit()
            purged = photo_services.purge_expired_pending()
            db.session.remove()
        check("expired pending photo → clean-up discards it and deletes its images in the same commit",
              purged >= 1 and q("SELECT delete_reason FROM symptom_photos WHERE public_id = :p", p=d["id"]) == "expired_unattached"
              and n_blobs(d["id"]) == 0)
        check("no image row without a pending / attached photo", q(
            "SELECT count(*) FROM symptom_photo_blobs b JOIN symptom_photos p ON p.id = b.photo_id WHERE p.status = 'discarded'") == 0)

    elif PHASE == "restart":
        pt, nt = token("patient01@demo.local"), token("nurse01@demo.local")
        p2t, nt2, at = token("p2@demo.local"), token("nurse02@demo.local"), token("admin01@demo.local")
        r = content(nt, state["A"])
        check("process 2 (= after a redeploy): the nurse reads the image, bytes = the checksum recorded at upload",
              r.status_code == 200 and hashlib.sha256(r.data).hexdigest() == state["sha_a"])
        check("…thumbnail and the patient's own read → 200", content(nt, state["A"], "thumb").status_code == 200 and content(pt, state["A"]).status_code == 200)
        check("…another patient 404, unassigned nurse 404, admin 403", (content(p2t, state["A"]).status_code, content(nt2, state["A"]).status_code,
                                                                        content(at, state["A"]).status_code) == (404, 404, 403))
        statements = []
        with app.app_context():
            fn = lambda conn, cursor, statement, params, context, many: statements.append(statement)  # noqa: E731
            event.listen(db.engine, "before_cursor_execute", fn)
            codes = [c.get(f"/api/v1/patients/{state['patient']}/symptom-photos", headers=H(nt)).status_code,
                     c.get("/api/v1/patients/me/symptom-photos", headers=H(pt)).status_code,
                     c.get("/api/v1/symptoms/records/me?form_code=rt_skin_self_assessment", headers=H(pt)).status_code,
                     c.get(f"/api/v1/symptoms/records/{state['patient']}", headers=H(nt)).status_code]
            event.remove(db.engine, "before_cursor_execute", fn)
        check(f"PostgreSQL: history / record lists answer 200 and none of their {len(statements)} statements touches symptom_photo_blobs",
              codes == [200] * 4 and not any("symptom_photo_blobs" in s for s in statements), codes)

    # --------------------------------------------------------------------------------------------- concurrency
    elif PHASE == "concurrency":
        lock = os.environ["PHOTO_CHECK_LOCK"] == "on"
        if not lock:  # control run: the same check without the advisory lock
            def reserve_without_lock(self, nbytes):
                if self.used_bytes() + nbytes > self.quota_bytes:
                    raise PhotoStorageFull("photo quota exceeded", "QuotaExceeded")
            DatabasePhotoStorage.reserve = reserve_without_lock
        real_used = DatabasePhotoStorage.used_bytes

        def slow_used(self):  # widen the window between the quota check and the insert
            value = real_used(self)
            time.sleep(0.4)
            return value
        DatabasePhotoStorage.used_bytes = slow_used
        users = [f"cc{i:02d}@demo.local" for i in range(1, 9)]
        toks = [token(u) for u in users]
        for t in toks:
            consent(t)
        images = [jpeg() for _ in users]
        with app.app_context():
            needs = [len(i.full) + len(i.thumb) for i in (photo_services.prepare_upload(d, "image/jpeg") for d in images)]
            base = real_used(storage)
            db.session.remove()
        storage.quota_bytes = base + sum(sorted(needs)[:3])  # room for about three of the eight
        results, barrier = [None] * 8, threading.Barrier(8)

        def go(i):
            client = app.test_client()
            barrier.wait()
            results[i] = upload(toks[i], images[i], client)[0].status_code
        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        start = time.monotonic()
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        elapsed = time.monotonic() - start
        after = used()
        over = after - storage.quota_bytes
        tag = "with the advisory lock" if lock else "CONTROL, lock removed"
        print(f"[INFO] {tag}: 8 concurrent uploads → {sorted(results)} in {elapsed:.1f} s; used − quota = {over} bytes", flush=True)
        if lock:
            check("8 concurrent uploads with the advisory lock: some 201, the rest 507 PHOTO_STORAGE_FULL, none 500",
                  set(results) <= {201, 507} and 201 in results and 507 in results, results)
            check("…stored image bytes never exceed the quota", over <= 0, over)
            check("…the quota check and insert ran one after the other (≥ 8 × the injected 0.4 s)", elapsed >= 8 * 0.4)
            state["concurrent_ok"] = results.count(201)
        else:
            check("control run without the lock: the same race overshoots the quota (the test detects the problem)", over > 0, over)
        save()

    # --------------------------------------------------------------------------------------------- 200 MiB
    elif PHASE == "fill":
        patient_id, user_id = state["filler"]
        target = QUOTA - 2 * 1024 * 1024  # leave room for a few real uploads
        n = 0
        with app.app_context():
            while storage.used_bytes() < target:
                size = min(5 * 1024 * 1024, target - storage.used_bytes())
                folder = uuid.uuid4().hex
                p = SymptomPhoto(patient_id=patient_id, uploaded_by=user_id, status="pending", storage_backend="database",
                                 storage_key=f"photos/{folder}/full.jpg", thumb_storage_key=f"photos/{folder}/thumb.jpg", content_type="image/jpeg",
                                 file_size=size, width=1600, height=1200, thumb_file_size=1024, checksum_sha256="0" * 64, source_sha256=uuid.uuid4().hex * 2)
                db.session.add(p)
                db.session.flush()
                for variant, nbytes in (("full", size), ("thumb", 1024)):
                    data = os.urandom(nbytes)
                    db.session.add(SymptomPhotoBlob(photo_id=p.id, variant=variant, storage_key=getattr(p, "storage_key" if variant == "full" else "thumb_storage_key"),
                                                    content_type="image/jpeg", byte_size=nbytes, sha256=hashlib.sha256(data).hexdigest(), data=data))
                db.session.commit()
                n += 1
            print(f"[INFO] filled with {n} synthetic photos (random bytes) to {storage.used_bytes() / 1048576:.1f} MiB of the 200 MiB quota", flush=True)
            db.session.remove()

    elif PHASE == "full":
        check("quota from the environment: 209715200 bytes (200 MiB)", storage.quota_bytes == QUOTA)
        pt, nt = token("patient01@demo.local"), token("nurse01@demo.local")
        uploaders = [token(f"cc{i:02d}@demo.local") for i in range(1, 9)]  # spread: each patient may upload 8 per 10 min
        statuses = []
        for i in range(24):
            r, _d = upload(uploaders[i % len(uploaders)])
            statuses.append(r.status_code)
            if r.status_code != 201:
                last = r
                break
        check(f"uploads until the 200 MiB quota: {statuses.count(201)} × 201, then 507 PHOTO_STORAGE_FULL", statuses[-1] == 507
              and code(last) == "PHOTO_STORAGE_FULL" and set(statuses[:-1]) <= {201}, statuses)
        u = used()
        check(f"…stored image bytes {u} ≤ quota {QUOTA}", u <= QUOTA)
        check("…the 507 response contains no patient data", not [w for w in PII if w in last.get_data(as_text=True)])
        check("storage full: a text-only skin report still works (201)", report(pt).status_code == 201)
        check("storage full: chat still works (201)", c.post("/api/v1/patients/me/messages", json={"body": "照片無法上傳，改用文字"}, headers=H(pt)).status_code == 201)
        check("storage full: stored images are still readable", content(nt, state["A"]).status_code == 200)
        state["full_used"] = u
        save()

    # --------------------------------------------------------------------------------------------- restore
    elif PHASE == "restored":
        nt = token("nurse01@demo.local")
        r = content(nt, state["A"])
        check("restored database, new process: the image reads back identical (checksum)", r.status_code == 200
              and hashlib.sha256(r.data).hexdigest() == state["sha_a"])
        n, total = q("SELECT count(*) FROM symptom_photo_blobs"), q("SELECT coalesce(sum(byte_size), 0) FROM symptom_photo_blobs")
        check("…all image rows and bytes restored", n == state["dump_rows"] and total == state["dump_bytes"], (n, total))
        check("…every restored image still matches its sha256",
              q("SELECT count(*) FROM symptom_photo_blobs WHERE encode(sha256(data), 'hex') <> sha256") == 0)
        check("…BYTEA storage setting kept (EXTERNAL)",
              q("SELECT attstorage FROM pg_attribute WHERE attrelid='symptom_photo_blobs'::regclass AND attname='data'") == "e")

    elif PHASE == "totals":
        state["dump_rows"] = q("SELECT count(*) FROM symptom_photo_blobs")
        state["dump_bytes"] = q("SELECT coalesce(sum(byte_size), 0) FROM symptom_photo_blobs")
        save()

    print(f"{sum(ok)}/{len(ok)} checks passed")
    return 0 if all(ok) else 1


# =============================================================================== orchestrator
def free_port():
    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        return s_.getsockname()[1]


def main():
    if os.environ.get("PG_BIN"):  # another PostgreSQL install, e.g. 17 like Render
        pgbin = Path(os.environ["PG_BIN"])
    else:
        from pgserver import _commands  # local test tool (PostgreSQL 16)
        pgbin = Path(_commands.POSTGRES_BIN_PATH)
    exe = lambda name: str(pgbin / (name + (".exe" if os.name == "nt" else "")))  # noqa: E731
    tmp = Path(tempfile.mkdtemp(prefix="photo-db-check-"))
    port = free_port()
    print(f"===== database photo storage on a throw-away PostgreSQL ({subprocess.run([exe('postgres'), '--version'], capture_output=True, text=True).stdout.strip()}, 127.0.0.1:{port})")
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        subprocess.run([exe("initdb"), "-D", str(tmp / "data"), "-U", "postgres", "-A", "trust", "-E", "UTF8"], check=True, **quiet)
        # not captured: the server started by pg_ctl inherits the handles, a capture would wait for it forever
        subprocess.run([exe("pg_ctl"), "-D", str(tmp / "data"), "-o", f"-p {port} -h 127.0.0.1", "-l", str(tmp / "pg.log"), "-w", "start"],
                       check=True, **quiet)
    except subprocess.CalledProcessError:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)  # nothing left behind when the server cannot be set up
        raise
    url = lambda name: f"postgresql://postgres@127.0.0.1:{port}/{name}"  # noqa: E731
    pg = ["-h", "127.0.0.1", "-p", str(port), "-U", "postgres"]
    results, outputs = [], []
    consent_version = re.search(r'^CONSENT_VERSION = "([^"]+)"', (BACKEND / "app/modules/photo/consent.py").read_text(encoding="utf-8"), re.M).group(1)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PHOTO_", "AWS_", "SEED_", "DATABASE_"))}
    env.update(FLASK_ENV="staging", CORS_ORIGINS="https://photo-db-check.invalid", PYTHONIOENCODING="utf-8",
               SECRET_KEY=secrets.token_hex(32), JWT_SECRET_KEY=secrets.token_hex(32),
               PHOTO_UPLOADS_ENABLED="true", PHOTO_STORAGE_BACKEND="database", PHOTO_DB_QUOTA_BYTES=str(QUOTA),
               PHOTO_CONSENT_APPROVED_VERSION=consent_version, PHOTO_CHECK_STATE=str(tmp / "state.json"), DATABASE_URL=url("ccp"))

    def run(args, **extra):
        p = subprocess.run(args, cwd=BACKEND, env={**env, **extra}, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", timeout=900)
        outputs.append(p.stderr)  # the application's log output
        return p

    def phase(name, **extra):
        p = run([sys.executable, __file__], PHOTO_CHECK_PHASE=name, **extra)
        lines = [ln for ln in p.stdout.splitlines() if ln.startswith(("[PASS]", "[FAIL]", "[INFO]"))]
        print("\n".join(lines))
        results.extend(ln.startswith("[PASS]") for ln in lines if not ln.startswith("[INFO]"))
        if p.returncode != 0 and not any(ln.startswith("[FAIL]") for ln in lines):
            print(f"[FAIL] phase {name} crashed (exit {p.returncode})\n{p.stderr[-2500:]}")
            results.append(False)

    def expect(label, cond, detail=""):
        results.append(bool(cond))
        print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""))

    flask = [sys.executable, "-m", "flask", "--app", "run"]
    try:
        run([exe("createdb"), *pg, "ccp"])
        print("----- 1. migration")
        p = run([*flask, "db", "upgrade"])
        expect("flask db upgrade (full chain incl. b8c3f1d7e2a6) on PostgreSQL", p.returncode == 0, p.stderr[-300:] if p.returncode else "")
        phase("schema")
        phase("seed")
        print("----- 2. upload, then a new process (redeploy), access, no BYTEA in list queries")
        phase("upload")
        phase("restart")
        phase("delete")
        print("----- 3. concurrent uploads against the quota")
        phase("concurrency", PHOTO_CHECK_LOCK="on")
        phase("concurrency", PHOTO_CHECK_LOCK="off")
        print("----- 4. the 200 MiB quota")
        phase("fill")
        phase("full")
        p = run([*flask, "photos", "storage-report"])
        print("\n".join("      " + ln for ln in p.stdout.splitlines()))
        expect("`flask photos storage-report` at the limit → critical, exit code 2", p.returncode == 2 and "critical" in p.stdout)
        expect("…shows the image table (incl. TOAST) and the database size", "image table:" in p.stdout and "MiB" in p.stdout.split("image table:")[1].splitlines()[0]
               and "database total:" in p.stdout)
        expect("…no patient data, photo id or key in the report", not [w for w in PII + ["photos/"] if w in p.stdout])
        p = run([*flask, "photos", "verify"])
        expect("`flask photos verify`: nothing missing, no images without a row", p.returncode == 0 and "0 missing" in p.stdout and "0 file(s) without a row" in p.stdout,
               p.stdout.strip()[:200])
        print("----- 5. backup and restore (pg_dump custom format → pg_restore into a new database)")
        phase("totals")
        t0 = time.monotonic()
        p = run([exe("pg_dump"), *pg, "-Fc", "-f", str(tmp / "ccp.dump"), "ccp"])
        dump_s = time.monotonic() - t0
        expect("pg_dump -Fc", p.returncode == 0, p.stderr[-300:])
        run([exe("createdb"), *pg, "ccp_restored"])
        t0 = time.monotonic()
        p = run([exe("pg_restore"), *pg, "--no-owner", "-d", "ccp_restored", str(tmp / "ccp.dump")])
        restore_s = time.monotonic() - t0
        expect("pg_restore into a new database", p.returncode == 0, p.stderr[-300:])
        size = (tmp / "ccp.dump").stat().st_size
        print(f"[INFO] dump {size / 1048576:.1f} MiB (images ≈ {json.loads((tmp / 'state.json').read_text())['dump_bytes'] / 1048576:.1f} MiB); "
              f"pg_dump {dump_s:.1f} s, pg_restore {restore_s:.1f} s (local disk)")
        phase("restored", DATABASE_URL=url("ccp_restored"))
        print("----- 6. downgrade guard")
        p = run([*flask, "db", "downgrade", "a9d4e2b7c5f1"], DATABASE_URL=url("ccp_restored"))
        rev = run([exe("psql"), *pg, "-d", "ccp_restored", "-tAc", "SELECT version_num FROM alembic_version"]).stdout.strip()
        expect("downgrade refused while images exist; revision unchanged", p.returncode != 0 and "Refusing to downgrade" in p.stdout + p.stderr
               and rev == "b8c3f1d7e2a6", rev)
        run([exe("createdb"), *pg, "ccp_empty"])
        up1 = run([*flask, "db", "upgrade"], DATABASE_URL=url("ccp_empty")).returncode
        down = run([*flask, "db", "downgrade", "a9d4e2b7c5f1"], DATABASE_URL=url("ccp_empty")).returncode
        up2 = run([*flask, "db", "upgrade"], DATABASE_URL=url("ccp_empty")).returncode
        expect("empty image table: downgrade to a9d4e2b7c5f1 and upgrade again", (up1, down, up2) == (0, 0, 0), (up1, down, up2))
        logs = "\n".join(outputs)
        expect("application logs (all phases) contain no patient e-mail, patient code or object key",
               not [w for w in ("patient01@demo.local", "p2@demo.local", "P00001", "P09002", "photos/") if w in logs])
    finally:
        subprocess.run([exe("pg_ctl"), "-D", str(tmp / "data"), "-m", "fast", "-w", "stop"], capture_output=True)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed — real PostgreSQL (throw-away), synthetic data")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(child() if PHASE else main())
