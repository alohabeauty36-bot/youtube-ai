#!/usr/bin/env bash
# Smoke test DEMO toàn tuyến 10 phút: seed → quét → Autopilot Private → dựng → upload DEMO (private) → Telegram DEMO → verify
set -euo pipefail
cd "$(dirname "$0")/.."
export APP_MODE=demo PYTHONPATH=backend
export DATABASE_URL="${DATABASE_URL:-sqlite:///./data/smoke.db}"
rm -f ./data/smoke.db
python3 -m ev_studio.cli demo | tee ./data/smoke.log
SID=$(python3 - <<'PY'
from ev_studio.db import SessionLocal
from ev_studio.models import Story
from sqlalchemy import select
with SessionLocal() as s:
    print(s.scalar(select(Story.id).order_by(Story.created_at.desc())))
PY
)
python3 -m ev_studio.cli verify "$SID"
