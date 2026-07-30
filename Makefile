.PHONY: install dev-api dev-web compose-up compose-down langfuse-up langfuse-down eval smoke test

install:
	pip install -e backend/packages/shared -e backend/packages/llm-gateway -e backend/packages/rag \
	  -e backend/packages/agent-core -e backend/packages/eval \
	  -e backend/apps/api -e backend/apps/rag-worker
	pip install -e ".[dev]"

dev-api:
	cd backend/apps/api && PYTHONPATH=. uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-web:
	cd frontend && npm install && npm run dev

compose-up:
	docker compose -f deploy/docker-compose.yml up -d --build

compose-down:
	docker compose -f deploy/docker-compose.yml down

langfuse-up:
	@test -f deploy/langfuse/.env || cp deploy/langfuse/.env.example deploy/langfuse/.env
	docker compose -f deploy/langfuse/docker-compose.yml --env-file deploy/langfuse/.env up -d

langfuse-down:
	docker compose -f deploy/langfuse/docker-compose.yml --env-file deploy/langfuse/.env down

eval:
	python scripts/run_eval.py --mode mock --kind all --fail-under hit_at_k=1.0,success_rate=1.0,skill_accuracy=1.0,faithfulness=1.0,relevancy=1.0

smoke:
	.venv/bin/python scripts/smoke_import.py

test:
	.venv/bin/python -m pytest tests/ -q
