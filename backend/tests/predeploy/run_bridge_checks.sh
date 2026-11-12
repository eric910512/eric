#!/usr/bin/env bash
# Bridge release checks on ISOLATED in-memory PostgreSQL (PGlite) — never production. Writes only to a temp dir.
# Usage (from the repository root):
#   bash backend/tests/predeploy/run_bridge_checks.sh [BRIDGE_BACKEND_DIR] [OLD_COMMIT] [FULL_COMMIT]
# BRIDGE_BACKEND_DIR defaults to the bridge worktree; OLD = deployed chat release, FULL = Phase 1c release.
set -u
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
BRIDGEB=${1:-/d/app-worktrees/phase1abc-schema-bridge/backend}
OLD=${2:-71e9d8e}
FULL=${3:-feature/skin-follow-up}  # the reviewed full release (a commit or this branch)
PY=${PYTHON:-$ROOT/venv/Scripts/python.exe}; [ -x "$PY" ] || PY=$ROOT/venv/bin/python
PGLITE=$ROOT/frontend/node_modules/@electric-sql/pglite-socket/dist/scripts/server.js
CHECKS=$ROOT/backend/tests/predeploy
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
export FLASK_ENV=staging CORS_ORIGINS=https://predeploy.invalid SEED_DEMO_PASSWORD=Predeploy123 PYTHONIOENCODING=utf-8
export SECRET_KEY=$($PY -c "import secrets;print(secrets.token_hex(32))") JWT_SECRET_KEY=$($PY -c "import secrets;print(secrets.token_hex(32))")
unset PHOTO_UPLOADS_ENABLED PHOTO_STORAGE_BACKEND SEED_DEMO_DATA ALLOW_DROP_CARE_FOLLOW_UPS
mkdir -p "$TMP/old" "$TMP/full"
(cd "$ROOT" && git archive "$OLD" backend | tar -x -C "$TMP/old" && git archive "$FULL" backend | tar -x -C "$TMP/full")
OLDB=$TMP/old/backend FULLB=$TMP/full/backend
FAIL=0
expect() { if [ "$2" = "$3" ]; then echo "[PASS] $1 ($2)"; else echo "[FAIL] $1 (got '$2', expected '$3')"; FAIL=1; fi; }
start_pg() {
  export DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:$1/postgres"
  node "$PGLITE" --db=memory:// --port="$1" --host=127.0.0.1 > "$TMP/pg-$1.log" 2>&1 & PG=$!
  $PY -c "
import socket,time
for _ in range(150):
    try: socket.create_connection(('127.0.0.1',$1),0.2).close(); break
    except OSError: time.sleep(0.1)"
}
baseline() { (cd "$OLDB" && BACKEND="$OLDB" $PY "$CHECKS/baseline_build.py") 2>&1 | grep baseline; }
stop_pg() { kill "$PG" 2>/dev/null; wait "$PG" 2>/dev/null; }
# the first step of render_start.sh (and, with SEED_DEMO_DATA=true, the second) — exit code
start_script() { (cd "$1" && $PY -m flask --app run db upgrade > "$TMP/start.log" 2>&1 || exit 1
                  if [ "${SEED_DEMO_DATA:-false}" = "true" ]; then $PY -m flask --app run seed dev >> "$TMP/start.log" 2>&1 || exit 1; fi); echo $?; }
sql() { $PY -c "
import os,time; from sqlalchemy import create_engine, text
e=create_engine(os.environ['DATABASE_URL'])
for _ in range(20):
    try: c=e.connect(); break
    except Exception: time.sleep(0.5)
r=c.execute(text(\"$1\")); c.commit()
print(r.scalar() if r.returns_rows else 'ok')"; }
mode() { (cd "$2" && MODE="$1" BACKEND="$2" SNAP="$TMP/snap.json" $PY "$CHECKS/bridge_check.py") 2>&1 | grep -E "^\[(PASS|FAIL)\]|checks passed" | tee -a "$TMP/modes.log"; }

echo "===== 1. bridge → full → rollback to bridge → full again"
start_pg 55461; baseline
mode bridge-upgrade "$BRIDGEB"
expect "bridge start script again (render_start step 1): no-op" "$(start_script "$BRIDGEB")" 0
expect "…no seed ran (SEED_DEMO_DATA unset)" "$(grep -c 'Seed complete' "$TMP/start.log")" 0
expect "full release start script after the bridge: nothing left to migrate" "$(start_script "$FULLB")" 0
expect "…migrations applied by the full release" "$(grep -c 'Running upgrade' "$TMP/start.log")" 0
mode full-writes "$FULLB"
expect "ROLLBACK full → bridge: bridge start script succeeds" "$(start_script "$BRIDGEB")" 0
mode bridge-serve "$BRIDGEB"
expect "forward again: full release start script" "$(start_script "$FULLB")" 0
mode full-again "$FULLB"
expect "for contrast — plain deployed release 71e9d8e start script on this database" "$(start_script "$OLDB")" 1
grep -q "Can't locate revision" "$TMP/start.log" && echo "      ($(grep -o "Can't locate revision identified by '[^']*'" "$TMP/start.log"))"
stop_pg

for n in 2 3; do
  table=$([ $n = 2 ] && echo symptom_photos || echo care_follow_ups)
  echo "===== $n. migration #$n of 3 fails (a '$table' table already exists) — bridge start script"
  start_pg $((55461 + n)); baseline
  sql "CREATE TABLE $table (id integer primary key)" > /dev/null
  expect "bridge start script fails" "$(start_script "$BRIDGEB")" 1
  grep -oE "Running upgrade [^,]*" "$TMP/start.log" | sed 's/^/      /'
  expect "…database stays at the chat revision (one transaction)" "$(sql 'SELECT version_num FROM alembic_version')" b5cd01c11e1d
  expect "…Phase 1a rows rolled back" "$(sql "SELECT COUNT(*) FROM symptom_forms WHERE code = 'rt_skin_self_assessment'")" 0
  expect "…other new tables absent" "$(sql "SELECT COUNT(*) FROM information_schema.tables WHERE table_name IN ('patient_consents','care_follow_up_events')")" 0
  expect "…chat service recovers: deployed release 71e9d8e starts" "$(start_script "$OLDB")" 0
  stop_pg
done

echo "===== 4. production revision is not b5cd01c11e1d"
start_pg 55465
(cd "$BRIDGEB" && $PY -m flask --app run db upgrade f1a7c3d2b9e4 > /dev/null 2>&1)
expect "(a) database still before the chat migration (f1a7c3d2b9e4): bridge start script" "$(start_script "$BRIDGEB")" 0
expect "…ends at the Phase 1c head" "$(sql 'SELECT version_num FROM alembic_version')" a9d4e2b7c5f1
stop_pg
start_pg 55466; baseline
sql "UPDATE alembic_version SET version_num = '0000deadbeef'" > /dev/null
expect "(b) unknown revision: bridge start script refuses" "$(start_script "$BRIDGEB")" 1
expect "…nothing changed" "$(sql 'SELECT version_num FROM alembic_version')" 0000deadbeef
stop_pg

echo "===== 5. dependencies: bridge migrations without Pillow / boto3"
start_pg 55467; baseline
(cd "$BRIDGEB" && $PY -c "
import sys; sys.modules['PIL'] = None; sys.modules['boto3'] = None
from flask.cli import main; sys.argv = ['flask', '--app', 'run', 'db', 'upgrade']; main()" > "$TMP/nopil.log" 2>&1; echo $? > "$TMP/nopil.rc")
expect "bridge upgrade with PIL and boto3 unavailable" "$(cat "$TMP/nopil.rc")" 0
expect "…reaches the Phase 1c head" "$(sql 'SELECT version_num FROM alembic_version')" a9d4e2b7c5f1

echo "===== 6. what SEED_DEMO_DATA=true would do on the bridge (risk evidence)"
train_before=$(sql "SELECT md5(string_agg(email || password_hash || role_id::text || is_active::text, ',' ORDER BY id)) FROM users WHERE email LIKE 'tr%'")
pairs_before=$(sql "SELECT md5(string_agg(nurse_id::text || '-' || patient_id::text || coalesce(ended_at::text,''), ',' ORDER BY id)) FROM nurse_patient_assignments")
demo_before=$(sql "SELECT md5(string_agg(password_hash, ',' ORDER BY id)) FROM users WHERE email IN ('patient01@demo.local','nurse01@demo.local','admin01@demo.local')")
expect "bridge start script with SEED_DEMO_DATA=true" "$(SEED_DEMO_DATA=true start_script "$BRIDGEB")" 0
expect "…training accounts unchanged (email, password hash, role, active)" "$(sql "SELECT md5(string_agg(email || password_hash || role_id::text || is_active::text, ',' ORDER BY id)) FROM users WHERE email LIKE 'tr%'")" "$train_before"
expect "…assignments unchanged" "$(sql "SELECT md5(string_agg(nurse_id::text || '-' || patient_id::text || coalesce(ended_at::text,''), ',' ORDER BY id)) FROM nurse_patient_assignments")" "$pairs_before"
demo_after=$(sql "SELECT md5(string_agg(password_hash, ',' ORDER BY id)) FROM users WHERE email IN ('patient01@demo.local','nurse01@demo.local','admin01@demo.local')")
[ "$demo_after" != "$demo_before" ] && echo "[INFO] …the 3 demo accounts' password hashes WERE rewritten by the seed (expected behaviour of SEED_DEMO_DATA=true)" \
  || echo "[INFO] …the 3 demo accounts' password hashes unchanged"
stop_pg

grep -q "^\[FAIL\]" "$TMP/modes.log" && FAIL=1
[ $FAIL = 0 ] && echo "ALL BRIDGE CHECKS PASSED" || { echo "SOME BRIDGE CHECKS FAILED"; exit 1; }
