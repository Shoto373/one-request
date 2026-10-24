# О!дин запрос (трек «О!дин запрос»)

AI-агент для путешествий: превращает одно сообщение («к тёплому морю в ноябре, недорого, с ребёнком») в готовую поездку — уточняет только нужное, подбирает и сравнивает комплекты (дорога, трансферы, отель), проверяет состыковки, пересобирает план при изменении условий и доводит до имитированной покупки.

- Видео-скринкаст: _TBD_
- Развёрнутый сервис: _TBD_

> Данные частично синтетические (цены, тарифы, расписания, условия отелей) — на карточках они помечены «сгенерировано». Оплата имитируется.

## Быстрый старт

```bash
git clone https://github.com/ostrovok-hackathon-2026/5-ukazatelei.git
cd 5-ukazatelei
cp .env.example .env      # для работы агента впишите LLM_API_KEY
docker compose up --build
# открыть http://localhost:8080
```

## Зависимости и переменные окружения

Рекомендованные ресурсы: 2 CPU, 4 GB RAM. Нужны только Docker и Docker Compose v2.

Сервисы: `app` (Python 3.12, FastAPI + React-сборка, порт 8080, один воркер) и `db` (PostgreSQL 16).

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `APP_PORT` | `8080` | Порт приложения в контейнере |
| `DATABASE_URL` | `postgresql://app:app@db:5432/trip` | Подключение к БД |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `app` / `app` / `trip` | Инициализация Postgres |
| `LOG_LEVEL` | `INFO` | Уровень логов |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Любой OpenAI-совместимый API |
| `LLM_API_KEY` | пусто | Ключ LLM. Без него UI открывается, а чат отвечает понятным `llm_not_configured`; для локальной Ollama не нужен |
| `LLM_MODEL` / `LLM_MODEL_FAST` | `gpt-4o-mini` | Модели |
| `LLM_TIMEOUT_SECONDS` | `30` | Таймаут запроса к LLM |
| `LLM_JSON_MODE` | `auto` | `auto` — JSON-режим, если провайдер поддерживает (иначе отключается сам); `off` — не отправлять `response_format` |

## Сидирование

Миграции (`db/migrations/*.sql`) и сид каталога (`data/seed/*.json`) применяются автоматически при старте `app`, повторный запуск ничего не дублирует. Начать с чистой БД: `make reset && make up`.

## Маршруты

- `/` — чат с агентом и «холст поездки»
- `/checkout/:cartId` — оформление (имитация оплаты), `/orders/:id` — заказ
- `/health` — `{"status":"ok"}`
- `/api/*` — REST + SSE, см. ниже

## Разработка

```bash
make up        # всё в Docker
make test      # тесты; интеграционные — на отдельной БД trip_test
make logs
make web-dev   # фронт с hot-reload на :5173, API → :8080
make web-mock  # фронт на моках, бэкенд не нужен
```

Без Docker: `pip install -r requirements.txt`, `DATABASE_URL` на свой Postgres 16, `uvicorn app.main:app --port 8080 --workers 1`.
Контракт фронт ↔ бэк: `web/src/api/types.ts`.

### Корзина и оплата (трек C)

- `POST /api/cart` `{session_id, version, bundle_id}` → `201 {cart_id, status, total_rub, snapshot}`. 409 `version_conflict`, если версия плана устарела; 422 `plan_blocked` / `total_mismatch`. Повторный вызов с теми же данными возвращает ту же корзину.
- `POST /api/cart/{id}/checkout` `{payer:{name,email}, idempotency_key}` → `201` новый заказ или `200` с `replay: true`, если заказ уже есть (тот же ключ или та же корзина). 409 `plan_changed`, если план изменился после добавления в корзину.
- `GET /api/cart/{id}`, `GET /api/orders/{id}` — заказ хранит неизменяемый снимок состава и цен.

## API contracts

- `GET /health` → `{"status":"ok"}`
- `POST /api/sessions` → `{"session_id":"<uuid>","version":0}`
- `GET /api/sessions/{id}` → `{"state": TripState, "messages": [...]}`
- `POST /api/sessions/{id}/messages` JSON: `{"request_id":"<uuid>","text":"Из Москвы в Сочи 10–17 ноября, двое взрослых","expected_version":0}`. Response content type is `text/event-stream`.
- `POST /api/sessions/{id}/commands` JSON: `{"request_id":"<uuid>","type":"accept_alternative","payload":{"alternative_id":"<confirmed-id>"},"expected_version":1}`. Supported types: `edit_requirement` (`key,value,kind?`), `remove_requirement` / `remove_assumption` (`key`), `select_destination` (`destination_id`), `accept_alternative` (`alternative_id`), `select_bundle` (`bundle_id`), `request_checkout` (empty payload). IDs must exist in the current state.
- `GET /api/sessions/{id}/requests/{request_id}` → request status, committed version and current version. It never replays old state or SSE.
- `GET /api/sessions/{id}/trace` → `{"session_id":"...","steps":[...]}`

SSE payloads are JSON, UTF-8, separated by a blank line:

```text
event: step\ndata: {"step_type":"llm","name":"parse_request","input":{...},"output":{...},"status":"ok"}

event: state\ndata: {"state":{"session_id":"...","version":1,"stage":"select",...}}

event: message\ndata: {"role":"assistant","text":"..."}

event: replay\ndata: {"request_id":"...","committed_version":2,"current_version":3,"reload":true}

event: done\ndata: {"ok":true,"version":1}
```

`step` records actual parse/tool inputs and results, not model reasoning. A stream-time failure is sent as `event: error` with `{code,message}` followed by `event: done` with `{ok:false}`. The state event contains the exact committed state. A stale `expected_version` returns HTTP 409 before stream headers; an unknown session returns 404; malformed request bodies return 422.

The frontend uses POST, so consume with `fetch`, not `EventSource`:

```js
const response = await fetch(`/api/sessions/${sessionId}/messages`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ request_id: crypto.randomUUID(), text: "Из Москвы в Сочи 10–17 ноября, двое взрослых", expected_version: 0 })
});
if (response.status === 409) { /* reload GET /api/sessions/{id}; do not resend over the new version */ }
const reader = response.body.getReader();
const decoder = new TextDecoder();
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  consumeSseText(decoder.decode(value, { stream: true })); // parse event/data frames
}
```

Equivalent manual calls:

```sh
BASE_URL=http://localhost:8080
SESSION_ID=$(curl -fsS -X POST "$BASE_URL/api/sessions" | python3.12 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')
curl -N -X POST "$BASE_URL/api/sessions/$SESSION_ID/messages" \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"11111111-1111-4111-8111-111111111111","text":"Из Москвы в Сочи 10–17 ноября, двое взрослых","expected_version":0}'
curl -fsS "$BASE_URL/api/sessions/$SESSION_ID"
```

The last call returns the persisted state and messages. Track A's `build_bundles`, destination suggestions and `replan(requirements, state, bundle_id, changes)` are wired through B adapters. The replan input contains the whole current requirement set, original selected bundle and accumulated diff since it was selected. Climate refs such as `climate:AYT:11.sea_temp` resolve to `{value,label,unit,kind,source,source_refs}` in the destination `sources` map for UI tooltips. Monthly climate values are statistics, not forecasts.

SSE `step` payloads use `{step_type,name,input,output,status}`, `state` uses `{state: TripState}`, `message` uses `{role,text}`, `replay` indicates a completed request whose current state must be fetched, and `done` uses `{ok,version?}`. Commands use the same SSE event schema. `TripState.open_questions` items have `{key,question,why,options}`; removed assumptions appear in `suppressed_assumptions` so the UI can remove them without the next turn restoring them. Mutations require `request_id`: the same ID and body is applied once; reusing it with different content is rejected. An in-flight duplicate gets HTTP 409 `request_in_progress`; a completed retry gets an SSE `replay` acknowledgment, never a stale state replay. For either 409, GET the session and ask the user before retrying an edit against a newer version. On an SSE disconnect the accepted request continues to commit; retry once with the same ID or poll the request-status endpoint. SSE replay is not implemented.

Run the fixed scenarios with `python -m eval.runner --mode deterministic` or against an isolated running API with `python -m eval.runner --mode e2e --base-url http://localhost:8080`. The deterministic mode requires `EVAL_RUNTIME_FACTORY=module:factory`; its factory returns an isolated engine with a stub LLM and declares substitutions on `engine.substitutions`. The E2E mode uses the real running server, its configured LLM and A tools. Both modes use the same YAML actions as the scripted user and write JSON reports under `eval/reports/` by default.

## LLM

Configure `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_MODEL_FAST`, and optionally `LLM_TIMEOUT_SECONDS`. `chat`, `chat_json`, and `chat_tools` are asynchronous; transient timeouts/network/408/429/5xx responses have at most two retries. `chat_json` allows one bounded format/schema correction attempt within a shared four-request maximum. Logs go to stdout and include model, duration and provider-reported usage only; credentials are never logged.

Провайдеры. Клиент сам сглаживает различия OpenAI-совместимых API: если провайдер отвергает `response_format` (Ollama, часть прокси) или `temperature` (reasoning-модели), запрос повторяется без параметра, и это запоминается; `content` из списка частей, `<think>…</think>`, ```-ограждения и текст вокруг JSON разбираются; `LLM_BASE_URL` можно вставить с хвостом `/chat/completions`; локальному серверу (localhost, `host.docker.internal`, `ollama`) ключ не нужен. Один и тот же ход агента проверен на трёх профилях провайдеров (строгий OpenAI-подобный, Ollama-подобный, reasoning) в `tests/test_llm_providers.py`.

Проверка конкретного провайдера: `python -m app.agent.smoke` (или с переменными, например `LLM_BASE_URL=https://openrouter.ai/api/v1 LLM_API_KEY=... LLM_MODEL=... python -m app.agent.smoke`) — обычный ответ, JSON по схеме и найденные возможности; код выхода 0 только при успехе.

Режим без ключа: UI и `/health` работают, `GET /api/status` → `{"llm": {"configured": false, ...}, "message": "Агенту не задан ключ LLM: …"}`, а сообщение в чате возвращает SSE-ошибку `llm_not_configured` с тем же текстом.

## Tests

```sh
pytest -q
```

Unit tests use mocked LLM responses and in-memory test stores. PostgreSQL integration tests are skipped unless `TEST_DATABASE_URL` points to a dedicated test database. Never point it at the shared/development database. The real `build_bundles` regression uses the integrated track A module. Checkout tests (`tests/test_cart.py`) also need `TEST_DATABASE_URL`; `make test` creates `trip_test` in the compose db and runs everything.

## Integration handoff

- **A:** implement `app.planning.suggest.suggest_destinations(requirements: dict, state: TripState) -> {destinations, alternatives, notes, required_inputs}`. A candidate has `feasible: true` only after A verifies it. Each alternative needs a stable `id`, `label`, `confirmed: true`, exact `changes`, refs and resolvable `evidence` (`ref,value,label,kind,source,source_refs`); B does not calculate alternatives. Assumptions are excluded from filter inputs; A may read them from state for ranking only.
- **A / B replan contract:** `app.planning.replan.replan(requirements, state, bundle_id, changes)` returns A's single `bundle` (or `None`), `kept`, `replaced`, `notes`, `price_delta_rub`, `alternatives`, and echoed `input_changes`. `requirements` is the full current set; `state` carries the original selected bundle; `changes` is the accumulated typed diff from the requirements at selection time. B adapts the result to the agent state shape and verifies bundle totals and price delta; A remains responsible for search, checks and price calculations. Keep/replacement entries need offer IDs and a reason so B can distinguish a retained catalog object from a repriced offer.
- **C request recovery:** send one `request_id` UUID per user mutation and reuse it only for byte-equivalent retries. On HTTP 409 `version_conflict`, reload `GET /api/sessions/{id}` and get user confirmation before applying their edit to the new version. On `request_in_progress`, poll `GET /requests/{request_id}`; on `completed`, reload session and trace. The endpoint does not replay prior SSE. A changed request body needs a fresh ID. An accepted request continues after client disconnect and commits atomically with messages/state/trace.
- **C:** consume `open_questions`, `advantages`, `tradeoffs`, `risks` and history; send commands through POST and parse SSE from `fetch`. For explicit agent stages, send `select_bundle` with `{bundle_id}` then `request_checkout` with `{}` and the latest `expected_version`; create the cart with `{session_id, version, bundle_id}` only after a non-blocking validation result. The cart and checkout endpoints independently enforce current version, offer availability/prices and blocking checks. Checkout remains C-owned. Keep one app worker because per-session serialization locks are process-local.
