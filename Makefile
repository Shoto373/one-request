.PHONY: up down build logs ps reset test eval-deterministic eval-e2e rehearsal web-dev web-mock

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

test:          ## тесты бэкенда; интеграционные — на отдельной БД trip_test в контейнере db
	-docker compose exec -T db sh -c 'createdb -U "$$POSTGRES_USER" trip_test 2>/dev/null'
	docker build --target test -t odin-zapros/test:dev .
	docker run --rm --network odin-zapros_default --env-file .env -e TEST_DATABASE_URL=postgresql://app:app@db:5432/trip_test odin-zapros/test:dev

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
