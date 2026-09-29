#!/usr/bin/env bash
# Render start command (runs from backend/). Works on every plan, including Free, which has
# no pre-deploy command or shell:
#   1. apply migrations (Alembic, idempotent; never generates new ones)
#   2. optionally load / refresh the synthetic demo data (staging only; SEED_DEMO_DATA=true)
#   3. start Gunicorn
# create_app() refuses to start with unsafe settings, so a misconfigured deploy fails here.
set -euo pipefail

flask --app run db upgrade

if [ "${SEED_DEMO_DATA:-false}" = "true" ]; then
  flask --app run seed dev
fi

exec gunicorn -c gunicorn.conf.py run:app
