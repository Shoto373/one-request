.PHONY: up down build logs ps reset test web-dev web-mock

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

web-dev:       ## фронт с hot-reload на :5173, API → :8080
	cd web && npm install && npm run dev

web-mock:      ## фронт на моках, бэкенд не нужен
	cd web && npm install && npm run dev:mock
