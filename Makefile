PY ?= python3
export PYTHONPATH := backend

run:                ## MỘT LỆNH: cài (lần đầu) + chạy + mở trình duyệt
	$(PY) run.py

install:            ## cài backend + frontend
	$(PY) -m pip install -e "backend[dev]"
	cd frontend && npm ci

test:               ## toàn bộ test (unit + integration + E2E DEMO rút gọn)
	$(PY) -m pytest tests -q

demo:               ## chạy toàn tuyến DEMO 10 phút (không upload thật)
	bash scripts/smoke_demo.sh

migrate:
	alembic upgrade head

api:
	uvicorn ev_studio.api.main:app --reload --port 8000 --app-dir backend

web:                ## dev giao diện (hot reload) trỏ về API :8000
	cd frontend && NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev

build-web:          ## build giao diện tĩnh vào backend/ev_studio/web_dist
	$(PY) run.py --build-web --no-browser

check-privacy:      ## kiểm tra integrity + privacy readback một story
	$(PY) -m ev_studio.cli verify $(STORY)
