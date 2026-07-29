.PHONY: install dev-api dev-web compose-up compose-down eval

install:
	pip install -e backend/packages/shared -e backend/packages/llm-gateway -e backend/packages/rag \
	  -e backend/packages/agent-core -e backend/packages/eval \
	  -e backend/apps/api -e backend/apps/rag-worker

dev-api:
	cd backend/apps/api && PYTHONPATH=. uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-web:
	cd frontend && npm install && npm run dev

compose-up:
	docker compose -f deploy/docker-compose.yml up -d --build

compose-down:
	docker compose -f deploy/docker-compose.yml down

eval:
	python scripts/run_eval.py

smoke:
	.venv/bin/python scripts/smoke_import.py

test:
	.venv/bin/python -m pytest tests/ -q
