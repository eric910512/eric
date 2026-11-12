#!/usr/bin/env bash
# Pre-deployment checks for Phase 1a～1c on an ISOLATED, in-memory PostgreSQL (PGlite) — never production.
# Needs: the frontend's node_modules (PGlite), the backend venv, git. Writes only to a temporary directory.
#
#   A. baseline built by the deployed chat code (71e9d8e) → stepwise upgrade to Phase 1c with the new code,
#      protected data unchanged, no schema drift, production-like smoke (staging, photos off), downgrade guard
#      (tests/predeploy/upgrade_path_check.py); then the OLD code's start script on that database (must fail:
#      "Can't locate revision") while an already running old instance still serves; guarded downgrade.
#   B. the three migrations in ONE `flask db upgrade` are atomic (a failing Phase 1c step rolls back 1a and 1b).
#   C. BRIDGE build (deployed code + only the new migration files): schema-first upgrade, then rollback
#      target after the new code ran (its start script must succeed).
#
# Usage (from the repository root): bash backend/tests/predeploy/run_isolated_checks.sh [OLD_COMMIT] [NEW_COMMIT]
set -u
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
OLD=${1:-71e9d8e}
NEW=${2:-HEAD}
PY=${PYTHON:-$ROOT/venv/Scripts/python.exe}; [ -x "$PY" ] || PY=$ROOT/venv/bin/python
PGLITE=$ROOT/frontend/node_modules/@electric-sql/pglite-socket/dist/scripts/server.js
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
export FLASK_ENV=staging CORS_ORIGINS=https://predeploy.invalid SEED_DEMO_PASSWORD=Predeploy123 PYTHONIOENCODING=utf-8
export SECRET_KEY=$($PY -c "import secrets;print(secrets.token_hex(32))") JWT_SECRET_KEY=$($PY -c "import secrets;print(secrets.token_hex(32))")
unset PHOTO_UPLOADS_ENABLED PHOTO_STORAGE_BACKEND SEED_DEMO_DATA ALLOW_DROP_CARE_FOLLOW_UPS

mkdir -p "$TMP/old" "$TMP/new" "$TMP/bridge"
# the release: a commit (git archive) or a checkout directory (its working tree, e.g. changes not committed yet;
# instance/, .env and caches are left out)
export_backend() { if [ -d "$1/backend" ]; then tar -C "$1" --exclude=backend/instance --exclude=backend/.env --exclude=__pycache__ -cf - backend | tar -x -C "$2"
                   else (cd "$ROOT" && git archive "$1" backend | tar -x -C "$2"); fi; }
(cd "$ROOT" && git archive "$OLD" backend | tar -x -C "$TMP/old" && git archive "$OLD" backend | tar -x -C "$TMP/bridge")
export_backend "$NEW" "$TMP/new"
for f in $(cd "$TMP/new/backend/migrations/versions" && ls *.py); do
  [ -f "$TMP/old/backend/migrations/versions/$f" ] || cp "$TMP/new/backend/migrations/versions/$f" "$TMP/bridge/backend/migrations/versions/$f"
done
OLDB=$TMP/old/backend NEWB=$TMP/new/backend BRIDGEB=$TMP/bridge/backend
CHECKS=$ROOT/backend/tests/predeploy  # these check scripts run from the working tree against the code under test

start_pg() {
  export DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:$1/postgres"
  node "$PGLITE" --db=memory:// --port="$1" --host=127.0.0.1 > "$TMP/pglite-$1.log" 2>&1 & PG=$!
  $PY -c "
import socket,time
for _ in range(150):
    try: socket.create_connection(('127.0.0.1',$1),0.2).close(); break
    except OSError: time.sleep(0.1)"
  (cd "$OLDB" && BACKEND="$OLDB" $PY "$CHECKS/baseline_build.py") 2>&1 | grep baseline
}
stop_pg() { kill "$PG" 2>/dev/null; wait "$PG" 2>/dev/null; }
up() { (cd "$1" && $PY -m flask --app run db upgrade > "$TMP/up.log" 2>&1; echo $?); }
sql() { $PY -c "
import os,time; from sqlalchemy import create_engine, text
e=create_engine(os.environ['DATABASE_URL'])
for _ in range(20):
    try: c=e.connect(); break
    except Exception: time.sleep(0.5)
print(c.execute(text(\"$1\")).scalar())"; }
FAIL=0
expect() { if [ "$2" = "$3" ]; then echo "[PASS] $1 ($2)"; else echo "[FAIL] $1 (got $2, expected $3)"; FAIL=1; fi; }

echo "===== A. stepwise upgrade, smoke, old code, downgrade guard"
start_pg 55451
(cd "$NEWB" && BACKEND="$NEWB" $PY "$CHECKS/upgrade_path_check.py") > "$TMP/a.log" 2>&1 || FAIL=1
grep -E "^\[(PASS|FAIL)\]|checks passed" "$TMP/a.log"
expect "old code start script on the Phase 1c database fails (Can't locate revision)" "$(up "$OLDB")" 1
expect "new code start script again: no-op" "$(up "$NEWB")" 0
stop_pg

echo "===== B. one upgrade run is atomic"
start_pg 55452
sql "SELECT 1" > /dev/null
$PY -c "
import os; from sqlalchemy import create_engine, text
with create_engine(os.environ['DATABASE_URL']).begin() as c: c.execute(text('CREATE TABLE care_follow_ups (id integer primary key)'))"
expect "upgrade with a failing Phase 1c step fails" "$(up "$NEWB")" 1
expect "…revision stays at the chat revision" "$(sql 'SELECT version_num FROM alembic_version')" b5cd01c11e1d
expect "…Phase 1a rows rolled back" "$(sql "SELECT COUNT(*) FROM symptom_forms WHERE code = 'rt_skin_self_assessment'")" 0
expect "…Phase 1b tables rolled back" "$(sql "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'symptom_photos'")" 0
stop_pg

echo "===== C. bridge build: schema-first and rollback target"
start_pg 55453
expect "bridge start script applies the new migrations" "$(up "$BRIDGEB")" 0
expect "…revision" "$(sql 'SELECT version_num FROM alembic_version')" b8c3f1d7e2a6
expect "new code start script after the bridge: no-op" "$(up "$NEWB")" 0
expect "rollback to the bridge: its start script succeeds" "$(up "$BRIDGEB")" 0
expect "rollback to the plain old commit: its start script fails" "$(up "$OLDB")" 1
stop_pg

[ $FAIL = 0 ] && echo "ALL ISOLATED PRE-DEPLOYMENT CHECKS PASSED" || { echo "SOME CHECKS FAILED"; exit 1; }
