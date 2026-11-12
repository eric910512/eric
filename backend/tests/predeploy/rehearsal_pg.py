"""Production upgrade REHEARSAL on a real, throw-away PostgreSQL — synthetic data only, never a deployed database.

    python tests/predeploy/rehearsal_pg.py [OLD] [BRIDGE] [FULL]      # from backend/
    defaults: 71e9d8e  feature/phase1abc-schema-bridge  feature/skin-follow-up
    PG_BIN=<PostgreSQL bin directory>   another install (Render runs 17); default: the `pgserver` package (16)

Each release is taken from git (`git archive`) and every step runs in its own process, like a deploy:

  1. OLD (deployed chat release) builds the simulated production database: demo seed, 50 training pairs, chat,
     a daily report and its review (baseline_build.py) — revision b5cd01c11e1d
  2. BRIDGE: its start step (`flask db upgrade`) applies the four new migrations; protected data byte-for-byte
     unchanged, no seed, old features work (bridge_check.py bridge-upgrade)
  3. FULL: start step migrates nothing; photos ON in this process only (PHOTO_STORAGE_BACKEND=database, 200 MiB
     quota): upload, new-process read, access isolation, removal / clean-up, concurrent quota; then Phase 1a / 1c
     data (bridge_check.py full-writes)
  4. ROLLBACK to BRIDGE: starts; old features served; all new-feature rows incl. photos and images unchanged
  5. FULL again: follow-ups and photos still readable (new process)
  6. contrast: the plain OLD release refuses the upgraded database ("Can't locate revision")
  7. backup / restore like a Render export: pg_dump directory format → .dir.tar.gz → extract → pg_restore into a NEW
     database; the FULL release starts on it, protected tables and every image identical
  8. failed upgrade: a database where the 4th migration fails → stays at b5cd01c11e1d, OLD starts again
"""
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OLD, BRIDGE, FULL = (sys.argv[1:] + ["71e9d8e", "feature/phase1abc-schema-bridge", "feature/skin-follow-up"][len(sys.argv[1:]):])[:3]
QUOTA = 200 * 1024 * 1024
PROTECTED = ("users", "patient_profiles", "nurse_patient_assignments", "chat_messages", "chat_read_states",
             "symptom_records", "symptom_record_values", "nursing_assessments", "notifications", "care_follow_ups",
             "care_follow_up_events", "patient_consents", "symptom_photos", "symptom_photo_blobs")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
results = []


def expect(label, cond, detail=""):
    results.append(bool(cond))
    print(f"[{'PASS' if cond else 'FAIL'}] {label}" + (f"  → {detail}" if detail else ""), flush=True)


def free_port():
    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        return s_.getsockname()[1]


def main():
    if os.environ.get("PG_BIN"):
        pgbin = Path(os.environ["PG_BIN"])
    else:
        from pgserver import _commands
        pgbin = Path(_commands.POSTGRES_BIN_PATH)
    exe = lambda name: str(pgbin / (name + (".exe" if os.name == "nt" else "")))  # noqa: E731
    tmp = Path(tempfile.mkdtemp(prefix="ccp-rehearsal-"))
    port = free_port()
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    version = subprocess.run([exe("postgres"), "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
    print(f"===== upgrade rehearsal on a throw-away PostgreSQL ({version}) — {OLD} → {BRIDGE} → {FULL}")
    try:
        subprocess.run([exe("initdb"), "-D", str(tmp / "data"), "-U", "postgres", "-A", "trust", "-E", "UTF8"], check=True, **quiet)
        subprocess.run([exe("pg_ctl"), "-D", str(tmp / "data"), "-o", f"-p {port} -h 127.0.0.1", "-l", str(tmp / "pg.log"), "-w", "start"],
                       check=True, **quiet)  # not captured: the server would keep the pipes open
    except subprocess.CalledProcessError:
        shutil.rmtree(tmp, ignore_errors=True)  # nothing left behind when the server cannot be set up
        raise
    pg = ["-h", "127.0.0.1", "-p", str(port), "-U", "postgres"]
    url = lambda name: f"postgresql://postgres@127.0.0.1:{port}/{name}"  # noqa: E731
    rel = {}
    for name, rev in (("old", OLD), ("bridge", BRIDGE), ("full", FULL)):
        (tmp / name).mkdir()
        archive = subprocess.run(["git", "archive", rev, "backend"], cwd=REPO, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(tmp / name)], input=archive, check=True)
        rel[name] = tmp / name / "backend"
        print(f"      {name:6s} = {rev} ({subprocess.run(['git', 'rev-parse', '--short', rev], cwd=REPO, capture_output=True, text=True).stdout.strip()})")
    consent = re.search(r'^CONSENT_VERSION = "([^"]+)"', (rel["full"] / "app/modules/photo/consent.py").read_text(encoding="utf-8"), re.M).group(1)
    base = {k: v for k, v in os.environ.items() if not k.startswith(("PHOTO_", "AWS_", "SEED_", "DATABASE_", "ALLOW_"))}
    base.update(FLASK_ENV="staging", CORS_ORIGINS="https://rehearsal.invalid", PYTHONIOENCODING="utf-8", SEED_DEMO_PASSWORD="Predeploy123",
                SECRET_KEY=secrets.token_hex(32), JWT_SECRET_KEY=secrets.token_hex(32), DATABASE_URL=url("ccp"))
    photos_on = dict(PHOTO_UPLOADS_ENABLED="true", PHOTO_STORAGE_BACKEND="database", PHOTO_DB_QUOTA_BYTES=str(QUOTA),
                     PHOTO_CONSENT_APPROVED_VERSION=consent, PHOTO_CHECK_STATE=str(tmp / "photo-state.json"))
    logs = []

    def run(args, cwd, **env):
        p = subprocess.run(args, cwd=cwd, env={**base, **env}, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", timeout=900)
        logs.append(p.stderr)
        return p

    def show(p, pattern=r"^\[(PASS|FAIL|INFO)\]|checks passed|^baseline"):
        lines = [ln for ln in p.stdout.splitlines() if re.search(pattern, ln)]
        print("\n".join("      " + ln if not ln.startswith("[") else ln for ln in lines), flush=True)
        results.extend(ln.startswith("[PASS]") for ln in lines if ln.startswith(("[PASS]", "[FAIL]")))
        if p.returncode != 0 and not any(ln.startswith("[FAIL]") for ln in lines):
            expect(f"step exited {p.returncode}", False, p.stderr[-1500:])

    def start(release, db="ccp", seed=False):
        """The first step of render_start.sh (`flask db upgrade`; then `flask seed dev` only with SEED_DEMO_DATA=true)."""
        p = run([sys.executable, "-m", "flask", "--app", "run", "db", "upgrade"], rel[release], DATABASE_URL=url(db))
        ran = re.findall(r"Running upgrade (\S+ -> \S+)", p.stderr)
        return p.returncode, ran, p.stderr

    def sql(db, query):
        return run([exe("psql"), *pg, "-d", db, "-tAc", query], tmp).stdout.strip()

    def fingerprint(db):
        return {t: sql(db, f"SELECT count(*) || ':' || coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '-') FROM {t} t") for t in PROTECTED}

    def bridge_check(mode, release, db="ccp"):
        show(run([sys.executable, str(HERE / "bridge_check.py")], rel[release], MODE=mode, BACKEND=str(rel[release]),
                 SNAP=str(tmp / "snap.json"), DATABASE_URL=url(db)))

    def photos(phase_, db="ccp"):
        show(run([sys.executable, str(HERE / "photo_db_storage_check.py")], rel["full"], PHOTO_CHECK_PHASE=phase_, BACKEND=str(rel["full"]),
                 PHOTO_CHECK_LOCK="on", DATABASE_URL=url(db), **photos_on))

    try:
        run([exe("createdb"), *pg, "ccp"], tmp)
        print("----- 1. OLD release: simulated production database")
        show(run([sys.executable, str(HERE / "baseline_build.py")], rel["old"], BACKEND=str(rel["old"])))
        expect("revision b5cd01c11e1d (the chat release)", sql("ccp", "SELECT version_num FROM alembic_version") == "b5cd01c11e1d")

        print("----- 2. BRIDGE deploy (schema first)")
        bridge_check("bridge-upgrade", "bridge")
        rc, ran, _ = start("bridge")
        expect("bridge start step again: exit 0, nothing left to migrate", rc == 0 and not ran, ran)

        print("----- 3. FULL deploy; photos on (this process only)")
        rc, ran, err = start("full")
        expect("full start step: exit 0, no migration, no seed", rc == 0 and not ran and "Seed complete" not in err, ran)
        for ph in ("seed", "upload", "restart", "delete", "concurrency"):
            photos(ph)
        bridge_check("full-writes", "full")

        print("----- 4. ROLLBACK to the BRIDGE")
        rc, ran, _ = start("bridge")
        expect("bridge start step on the upgraded database: exit 0", rc == 0 and not ran)
        bridge_check("bridge-serve", "bridge")

        print("----- 5. FULL again")
        rc, ran, _ = start("full")
        expect("full start step: exit 0", rc == 0 and not ran)
        bridge_check("full-again", "full")
        photos("restart")

        print("----- 6. contrast: the plain OLD release on the upgraded database")
        rc, _ran, err = start("old")
        expect("OLD start step refuses: Can't locate revision b8c3f1d7e2a6 (why the bridge is the rollback target)",
               rc != 0 and "Can't locate revision identified by 'b8c3f1d7e2a6'" in err)

        print("----- 7. backup / restore like a Render export (.dir.tar.gz)")
        photos("totals")
        before = fingerprint("ccp")
        t0 = time.monotonic()
        p = run([exe("pg_dump"), *pg, "-Fd", "-j", "2", "-f", str(tmp / "export.dir"), "ccp"], tmp)
        with tarfile.open(tmp / "export.dir.tar.gz", "w:gz") as tf:
            tf.add(tmp / "export.dir", arcname="export.dir")
        dump_s = time.monotonic() - t0
        expect("pg_dump directory format → .dir.tar.gz", p.returncode == 0, p.stderr[-300:])
        shutil.rmtree(tmp / "export.dir")
        (tmp / "restore").mkdir()
        with tarfile.open(tmp / "export.dir.tar.gz") as tf:
            tf.extractall(tmp / "restore", filter="data")
        run([exe("createdb"), *pg, "ccp_restored"], tmp)
        t0 = time.monotonic()
        p = run([exe("pg_restore"), *pg, "--no-owner", "-j", "2", "-d", "ccp_restored", str(tmp / "restore" / "export.dir")], tmp)
        restore_s = time.monotonic() - t0
        expect("pg_restore into a NEW, empty database", p.returncode == 0, p.stderr[-300:])
        size = (tmp / "export.dir.tar.gz").stat().st_size
        print(f"[INFO] export {size / 1048576:.1f} MiB; dump + tar {dump_s:.1f} s, pg_restore {restore_s:.1f} s", flush=True)
        after = fingerprint("ccp_restored")
        expect(f"restored: {len(PROTECTED)} tables identical row by row (users, patients, chat, records, follow-ups, photos, images)",
               after == before, {t: (before[t], after[t]) for t in PROTECTED if before[t] != after[t]})
        rc, ran, _ = start("full", db="ccp_restored")
        expect("FULL release starts on the restored database (nothing to migrate)", rc == 0 and not ran)
        photos("restored", db="ccp_restored")

        print("----- 8. failed upgrade (4th migration fails) on a separate database")
        run([exe("createdb"), *pg, "ccp_fail"], tmp)
        show(run([sys.executable, str(HERE / "baseline_build.py")], rel["old"], BACKEND=str(rel["old"]), DATABASE_URL=url("ccp_fail")))
        sql("ccp_fail", "CREATE TABLE symptom_photo_blobs (id integer PRIMARY KEY)")
        rc, ran, _ = start("bridge", db="ccp_fail")
        expect("bridge start step fails (exit ≠ 0) after running migrations 1–4", rc != 0 and len(ran) == 4, ran)
        expect("…database stays at b5cd01c11e1d (one transaction), no Phase 1a rows", sql("ccp_fail", "SELECT version_num FROM alembic_version") == "b5cd01c11e1d"
               and sql("ccp_fail", "SELECT count(*) FROM symptom_forms WHERE code = 'rt_skin_self_assessment'") == "0")
        rc, ran, _ = start("old", db="ccp_fail")
        expect("…the chat release starts again on it", rc == 0)

        leaked = [w for w in ("patient01@demo.local", "P00001", "photos/") if w in "\n".join(logs)]
        expect("application logs of every step: no patient e-mail, patient code or object key", not leaked, leaked)
    finally:
        subprocess.run([exe("pg_ctl"), "-D", str(tmp / "data"), "-m", "fast", "-w", "stop"], **quiet)
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed — {version}, throw-away database, synthetic data")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
