.PHONY: up down test test-live eval seed-sample frontend-check

up:
	docker compose up --build

down:
	docker compose down

test:
	cd backend && python -m pytest

test-live:
	cd backend && python -m pytest --live

# Runs the single-hop judge over eval/gold.jsonl. This spends API credit.
eval:
	cd backend && python ../eval/run_eval.py

seed-sample:
	docker compose exec backend drifttrace seed-sample

frontend-check:
	cd frontend && npm run lint && npm run build
