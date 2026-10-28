.PHONY: up down build logs ps reset seed test eval eval-deterministic eval-e2e rehearsal web-dev web-mock

up:            ## собрать и поднять
	@test -f .env || cp .env.example .env
	docker compose up --build -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f --tail=200

ps:
	docker compose ps

reset:         ## снести вместе с БД
	docker compose down -v

seed:          ## миграции + сид вручную (при старте app они применяются сами)
	docker compose exec app python -c "import os; from app.db import apply_migrations, load_seed; u = os.environ['DATABASE_URL']; apply_migrations(u); print('seed entries:', load_seed(u))"

test:          ## тесты бэкенда; интеграционные — на отдельной БД trip_test в контейнере db
	-docker compose exec -T db sh -c 'createdb -U "$$POSTGRES_USER" trip_test 2>/dev/null'
	docker build --target test -t odin-zapros/test:dev .
	docker run --rm --network odin-zapros_default --env-file .env -e TEST_DATABASE_URL=postgresql://app:app@db:5432/trip_test odin-zapros/test:dev

eval:          ## регрессия: все сценарии через настоящий подбор без LLM + независимые проверки снимка
	docker build --target test -t odin-zapros/test:dev .
	docker run --rm odin-zapros/test:dev python -m eval.plan_check --output /tmp/plan_check.json

eval-deterministic: ## фиксированный eval с инжектированным runtime (EVAL_RUNTIME_FACTORY)
	python -m eval.runner --mode deterministic

eval-e2e:       ## сквозной eval на отдельном API/БД и настроенном реальном LLM
	python -m eval.runner --mode e2e --base-url "$${EVAL_BASE_URL:-http://localhost:8080}"

rehearsal:      ## репетиция защиты: «чужие» запросы eval/rehearsal.txt по запущенному API
	python -m eval.rehearsal --base-url "$${EVAL_BASE_URL:-http://localhost:8080}"

web-dev:       ## фронт с hot-reload на :5173, API → :8080
	cd web && npm install && npm run dev

web-mock:      ## фронт на моках, бэкенд не нужен
	cd web && npm install && npm run dev:mock
