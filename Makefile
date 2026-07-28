.PHONY: install dev-api dev-web compose-up compose-down eval

install:
	pip install -e packages/shared -e packages/llm-gateway -e packages/rag \
	  -e packages/agent-core -e packages/eval -e apps/api -e apps/rag-worker

dev-api:
	cd apps/api && PYTHONPATH=. uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-web:
	cd apps/web && npm install && npm run dev

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
