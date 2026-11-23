PY ?= python3
export PYTHONPATH := backend

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

web:
	cd frontend && npm run dev

check-privacy:      ## kiểm tra integrity + privacy readback một story
	$(PY) -m ev_studio.cli verify $(STORY)
