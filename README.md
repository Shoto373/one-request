# О!дин запрос (трек «О!дин запрос»)

AI-агент для путешествий. Из одного сообщения («к тёплому морю в ноябре, недорого, с ребёнком») он делает готовую поездку: спрашивает только то, без чего нельзя, предлагает направления, собирает и сравнивает комплекты (дорога туда и обратно, трансферы, отель), проверяет стыковки, пересобирает план при изменении условий и доводит до имитированной оплаты.

- Видео-скринкаст: _TBD_
- Развёрнутый сервис: нет, только локальный запуск

> **По умолчанию используются демо-данные, оплата всегда имитируется.** Для live-режима Туту задайте `TRAVEL_DATA_PROVIDER=tutu`. Прототип поддерживает отдых только в Сочи; город отправления вводит пользователь, а фактическую доступность Туту проверяет отдельно для каждого маршрута. Live-ответы помечаются источником и временем получения; демо и live не подменяют друг друга.

## Быстрый старт

```bash
git clone https://github.com/ostrovok-hackathon-2026/5-ukazatelei.git
cd 5-ukazatelei
cp .env.example .env      # по желанию впишите LLM_API_KEY (любой OpenAI-совместимый провайдер)
docker compose up --build
# открыть http://localhost:8080
```

Больше ничего ставить не нужно: миграции и сид каталога применяются при старте контейнера `app`.

**Работает и без ключа LLM.** Если `LLM_API_KEY` не задан или провайдер не отвечает (лимит, таймаут, битый ответ), агент переходит в офлайн-режим: запрос разбирают правила (`app/agent/rule_parser.py` — города, даты, состав, дети, животные, бюджет, транспорт, по-русски и по-английски), а подбор, проверки, пересборка и оплата работают как обычно. В шапке появляется метка «офлайн-режим». С ключом агент понимает свободные формулировки лучше, без ключа проходит весь путь до покупки на демо-сценариях.

Сборка с нуля занимает около _N_ мин (замер ниже, в разделе [Проверка чистого запуска](#проверка-чистого-запуска)).

## Зависимости и переменные окружения

Рекомендованные ресурсы:
- 2 CPU cores
- 4 GB RAM
- около 2 GB диска под образы

Нужны только Docker и Docker Compose v2. Сервисы в `docker-compose.yml`:
- `app`: Python 3.12, FastAPI и собранный React-фронт. Порт 8080, один воркер, процесс работает не от root.
- `db`: PostgreSQL 16 (`postgres:16.6-alpine`), данные в volume `pgdata`.

Кэш и очереди не используются. LLM подключается через внешний OpenAI-совместимый API, своих моделей в репозитории нет.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `APP_PORT` | `8080` | Порт приложения в контейнере |
| `DATABASE_URL` | `postgresql://app:app@db:5432/trip` | Подключение к БД |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `app` / `app` / `trip` | Инициализация Postgres |
| `LOG_LEVEL` | `INFO` | Уровень логов (stdout, `docker compose logs app`) |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | Любой OpenAI-совместимый API |
| `LLM_API_KEY` | пусто | Ключ LLM. Без него агент работает в офлайн-режиме (разбор правилами); для локальной Ollama не нужен |
| `LLM_MODEL` / `LLM_MODEL_FAST` | `gpt-4o-mini` | Основная и быстрая модели |
| `LLM_TIMEOUT_SECONDS` | `30` | Таймаут одного запроса к LLM |
| `LLM_EXPLAIN_TIMEOUT_SECONDS` | `20` | Сколько ждать от LLM формулировку объяснения; дольше — берутся детерминированные утверждения по фактам |
| `LLM_JSON_MODE` | `auto` | `auto` — JSON-режим, если провайдер поддерживает (иначе отключается сам); `off` — не отправлять `response_format` |
| `TRAVEL_DATA_PROVIDER` | `demo` | `demo` или `tutu`; при ошибке live-поиска нет тихого возврата к демо |
| `TUTU_MCP_ENDPOINT` | `https://mcp.tutu.ru/mcp` | Streamable HTTP endpoint официального Tutu MCP |
| `TUTU_TIMEOUT_SECONDS` | `25` | Таймаут вызова MCP |
| `TUTU_MAX_CALLS` | `18` | Наш защитный бюджет MCP-вызовов на операцию поиска, не лимит поставщика |
| `TUTU_MAX_PARALLEL` | `2` | Наша максимальная параллельность запросов |

## Сидирование

Делать ничего не нужно. При старте `app` по порядку:
1. применяются миграции `db/migrations/*.sql`;
2. загружается сид `data/seed/*.json`;
3. каталог поездок (`data/seed/catalog/*.json`) читается в память.

Повторный запуск ничего не дублирует. Начать с чистой БД: `make reset && make up` (или `docker compose down -v && docker compose up --build`).

Каталог собран заранее и лежит в репозитории, при запуске ничего не скачивается. Пересборка каталога из снимков описана в [`pipeline/README.md`](pipeline/README.md).

## Маршруты и доступ

- `/` — чат с агентом и «холст поездки»: требования, направления, сравнение комплектов, таймлайн с проверками
- `/checkout/:cartId` — оформление (имитация оплаты)
- `/orders/:id` — заказ: неизменяемый снимок состава и цен
- `/metrics` — страница метрик по фиксированным сценариям, `/api/metrics` — то же в JSON
- `/health` — `200`, `{"status":"ok"}`; `/api/status` — настроен ли LLM
- `/api/*` — REST и SSE, контракт описан ниже, в разделе [Для разработчиков](#для-разработчиков)

Учётных записей нет. Сессия поездки создаётся автоматически и хранится в браузере, кнопка «Заново» начинает новую. На оплате подойдут любые имя и email: деньги не списываются.

## Демо-сценарий

1. «Хочу к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов». Агент показывает понятые требования (свои догадки помечает отдельно) и спрашивает город вылета, даты и возраст ребёнка. Потом предлагает три **выполнимых** направления с погодой, ценой всей поездки и временем в пути.
2. Выбор направления: три проверенных комплекта («дешевле», «удобнее», «гибче отмена»), у каждого общая цена и компромисс.
3. Таймлайн комплекта: ночной прилёт → проверка круглосуточной стойки отеля → предупреждение и вариант.
4. «А если на два дня позже и с нами собака?» Видно, что **сохранено** и что **заменено**, почему и на сколько изменилась цена.
5. Корзина → имитация оплаты → заказ-снимок. Повторное нажатие «Оплатить» второго заказа не создаёт.
6. Невозможный запрос («Неделя в Дубае на троих за 30 000»): честный ответ и два минимальных изменения, а не выдуманная покупка.
7. Поездом: «Из Москвы в Казань на выходные поездом».
8. Метрики по фиксированным сценариям: страница `/metrics`, см. [Метрики](#метрики).

Что ещё проверено: запрос на английском, «куда угодно», противоречия («дёшево, но 5 звёзд»), направления вне каталога («таких данных нет, ближайшее — …»), правки одного и того же плана из двух вкладок (409 и перечитывание), обрыв связи посреди ответа агента.

## Данные: что реальное, а что синтетическое

Каталог демо-сценариев небольшой и согласованный: 3 города отправления (Москва, Санкт-Петербург, Екатеринбург) × 8 направлений. Он нужен для офлайн-режима; это не покрытие live-поиска Туту. Единственное направление отдыха прототипа хранится в [`app/product_coverage.json`](app/product_coverage.json). Сейчас там Сочи (`AER`). Город отправления — пользовательский параметр, в live-режиме его разрешает и проверяет Туту. Москва не подставляется автоматически.

| Что | Реальное | Сгенерировано |
|---|---|---|
| Города, координаты, визовый режим для граждан РФ | Wikidata, справочник виз | — |
| Аэропорты и вокзалы | OurAirports, Wikidata | — |
| Климат по месяцам (воздух, море, дожди) | Open-Meteo, статистика 2021–2025 (это средние значения, а не прогноз) | — |
| Отели | название и координаты из OpenStreetMap | звёзды, номера, цены по ночам, питание, правила с животными, стойка регистрации, политика отмены |
| Рейсы | геометрия реальных аэропортов | расписания, номера рейсов, тарифы, багаж, цены |
| Поезда | реальные вокзалы | расписания, классы, цены |
| Трансферы | реальное расстояние хаб ↔ отель | классы машин, цены, время в пути |
| Оплата | — | имитация: деньги не списываются, заказ хранит снимок |

У каждого предложения есть флаг `synthetic` и ссылки на источник (`source_refs`). В интерфейсе синтетические позиции помечены «демо-данные». Генерация детерминированная (`seed = 42`), подробности в [`pipeline/README.md`](pipeline/README.md).

Атрибуция: © участники OpenStreetMap (ODbL); Weather data by Open-Meteo.com (CC BY 4.0); Wikidata (CC0); OurAirports (public domain).

### Live-данные Туту

Официальный endpoint `https://mcp.tutu.ru/mcp` использует MCP Streamable HTTP. Бэкенд инициализирует MCP-сессию и сверяет доступные инструменты со схемой при подключении. В live-режиме разрешены только `search_avia`, `search_rail`, `search_hotels` и `get_offer_details`. Инструменты регистрации пассажиров/создания заказов, ресурсы и прочие операции не доступны внутреннему агенту. Направление отдыха ограничено конфигурацией `app/product_coverage.json` (сейчас только Сочи); для поездки агент передаёт введённый город отправления в параметры поиска туда и обратно. Если Туту не разрешает город или возвращает неоднозначность, агент уточняет город и не переключает его сам.

`origin` и `destination` — отдельные поля требования, параметров поиска и нормализованного Offer. Destination policy централизован в `app/product_coverage.json`; `/api/status` публикует покрытие UI, а агент и команды выбора проверяют тот же конфиг. Расширение на несколько городов отдыха потребует добавить выбор направления в интерфейс и проверку фактического покрытия в конфигурации, не менять клиент MCP или общий нормализатор. Широкий поиск направлений по России не включён.

- Авиа: поиск туда-обратно, цена для переданного состава.
- ЖД: поиск туда и обратно отдельно. Показанная сумма основана на цене выбранного тарифа на одно место, умноженной на состав; детский тариф и наличие мест этого тарифа для всего состава могут быть неизвестны, поэтому такая позиция помечается как оценка и предупреждение.
- Отель: запрос на фактические даты и взрослых/возраст детей; при наличии вызываются детали тарифа. Цена — за весь срок проживания, не цена за ночь. Неизвестные правила/условия так и показываются.
- Пересборка повторяет ограниченный поиск с полными текущими требованиями. Разница считается кодом из новых и предыдущих сумм.
- Официальный MCP не предоставил инструмент поиска трансферов в проверенной выдаче. Трансфер остаётся открытым пунктом и не включён в сумму комплекта.
- Перед локальной имитацией checkout делается read-only поиск/получение деталей. Если точная позиция и цена не найдены в ограниченной выдаче либо источник недоступен, checkout блокируется. Это не резервирует предложение и не гарантирует поставщицкую цену.
- Ссылка поставщика показывается, только когда её вернул MCP. Она ведёт к поставщику; внутренний заказ проекта — снимок для демонстрации, без создания брони или списания денег.

Подробности официального доступа и его условий: [Tutu MCP](https://mcp.tutu.ru/mcp), [статья команды Туту](https://habr.com/ru/companies/tuturu/articles/1081246/).

Ограниченный read-only smoke отдельно от обычного pytest. Укажите город отправления; даты по умолчанию выбираются на четыре недели вперёд. Скрипт не вызывает бронирование или оплату:

```bash
docker compose exec app python -m scripts.tutu_live_smoke --live --origin Екатеринбург --departure 2026-11-25 --return-date 2026-12-02
docker compose exec app python -m scripts.tutu_planner_smoke --live --origin Казань --departure 2026-11-25 --return-date 2026-12-02
```

```bash
TRAVEL_DATA_PROVIDER=tutu python -m scripts.tutu_live_smoke --live
# Можно закрепить будущие даты явно:
TRAVEL_DATA_PROVIDER=tutu python -m scripts.tutu_live_smoke --live --departure 2026-11-22 --return-date 2026-11-29
# Вызов нормализующего planner и пересборка с полным накопленным diff:
TRAVEL_DATA_PROVIDER=tutu python -m scripts.tutu_planner_smoke --live --departure 2026-11-22 --return-date 2026-11-29
```

Скрипт не вызывает checkout, резервирование, регистрацию пассажиров или оплату. Интеграционные вызовы к поставщику не входят в обычный набор тестов.

## Метрики

Фиксированные сценарии лежат в `eval/scenarios`, `eval/sprint3` и `eval/sprint4`. В каждом есть запрос, скриптовые ответы пользователя (это не LLM, которая со всем соглашается) и условия успеха. Проверяются бюджет, состав гостей в номере и цене, даты, животное во всех позициях, отсутствие позиций со старыми датами после пересборки, а для невозможных запросов — честный отказ.

Страница `http://localhost:8080/metrics` (и JSON `GET /api/metrics`) прогоняет сценарии через настоящий подбор без LLM и показывает результат независимых проверок. Первый вызов считается несколько секунд.

```bash
make eval        # та же регрессия в тестовом образе
make eval-e2e    # по запущенному API с реальным LLM, отчёт в eval/reports/
```

Репетиция защиты: `make rehearsal` прогоняет 10 «чужих» запросов из `eval/rehearsal.txt` (их нет в фиксированных сценариях) и проверяет, что агент не ломается.

Успех — это доля сценариев, где выполнены условия, а не доля покупок: для невозможного запроса честный отказ тоже успех.

## Проверка чистого запуска

_Заполняется по результату замера._

## Разработка

```bash
make up        # всё в Docker
make down
make logs
make test      # тесты; интеграционные — на отдельной БД trip_test
make reset     # снести вместе с БД
make web-dev   # фронт с hot-reload на :5173, API → :8080
make web-mock  # фронт на моках, бэкенд и LLM не нужны
```

В режиме моков (`make web-mock`) сбои воспроизводятся тегом в сообщении: `#ошибка` — LLM не ответила, `#обрыв` — поток оборвался без ответа, `#конфликт` — план изменили в другой вкладке.

Без Docker: `pip install -r requirements.txt`, `DATABASE_URL` на свой Postgres 16, `uvicorn app.main:app --port 8080 --workers 1`.

## Для разработчиков

Контракт фронт ↔ бэк: `web/src/api/types.ts`.

### Корзина и оплата

- `POST /api/cart` `{session_id, version, bundle_id}` → `201 {cart_id, status, total_rub, snapshot}`. 409 `version_conflict`, если версия плана устарела; 422 `plan_blocked` / `total_mismatch`. Повторный вызов с теми же данными возвращает ту же корзину.
- `POST /api/cart/{id}/checkout` `{payer:{name,email}, idempotency_key}` → `201` новый заказ или `200` с `replay: true`, если заказ уже есть (тот же ключ или та же корзина). 409 `plan_changed`, если план изменился после добавления в корзину.
- `GET /api/cart/{id}`, `GET /api/orders/{id}` — заказ хранит неизменяемый снимок состава и цен.

### API contracts

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

### LLM

Configure `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_MODEL_FAST`, and optionally `LLM_TIMEOUT_SECONDS`. `chat`, `chat_json`, and `chat_tools` are asynchronous; transient timeouts/network/408/429/5xx responses have at most two retries. `chat_json` allows one bounded format/schema correction attempt within a shared four-request maximum. Logs go to stdout and include model, duration and provider-reported usage only; credentials are never logged.

Провайдеры. Клиент сам сглаживает различия OpenAI-совместимых API: если провайдер отвергает `response_format` (Ollama, часть прокси) или `temperature` (reasoning-модели), запрос повторяется без параметра, и это запоминается; `content` из списка частей, `<think>…</think>`, ```-ограждения и текст вокруг JSON разбираются; `LLM_BASE_URL` можно вставить с хвостом `/chat/completions`; локальному серверу (localhost, `host.docker.internal`, `ollama`) ключ не нужен. Один и тот же ход агента проверен на трёх профилях провайдеров (строгий OpenAI-подобный, Ollama-подобный, reasoning) в `tests/test_llm_providers.py`.

Проверка конкретного провайдера: `python -m app.agent.smoke` (или с переменными, например `LLM_BASE_URL=https://openrouter.ai/api/v1 LLM_API_KEY=... LLM_MODEL=... python -m app.agent.smoke`) — обычный ответ, JSON по схеме и найденные возможности; код выхода 0 только при успехе.

Режим без ключа: `GET /api/status` → `{"llm": {"configured": false, ...}, "agent_mode": "rules", "message": "LLM не настроен — агент работает в офлайн-режиме…"}`; ход агента проходит, в трейсе шаг `parse_request_rules`. Тот же откат на правила срабатывает при ошибке провайдера.

### Tests

```sh
pytest -q
```

Unit tests use mocked LLM responses and in-memory test stores. PostgreSQL integration tests are skipped unless `TEST_DATABASE_URL` points to a dedicated test database. Never point it at the shared/development database. The real `build_bundles` regression uses the integrated track A module. Checkout tests (`tests/test_cart.py`) also need `TEST_DATABASE_URL`; `make test` creates `trip_test` in the compose db and runs everything.

### Integration handoff

- **A:** implement `app.planning.suggest.suggest_destinations(requirements: dict, state: TripState) -> {destinations, alternatives, notes, required_inputs}`. A candidate has `feasible: true` only after A verifies it. Each alternative needs a stable `id`, `label`, `confirmed: true`, exact `changes`, refs and resolvable `evidence` (`ref,value,label,kind,source,source_refs`); B does not calculate alternatives. Assumptions are excluded from filter inputs; A may read them from state for ranking only.
- **A / B replan contract:** `app.planning.replan.replan(requirements, state, bundle_id, changes)` returns A's single `bundle` (or `None`), `kept`, `replaced`, `notes`, `price_delta_rub`, `alternatives`, and echoed `input_changes`. `requirements` is the full current set; `state` carries the original selected bundle; `changes` is the accumulated typed diff from the requirements at selection time. B adapts the result to the agent state shape and verifies bundle totals and price delta; A remains responsible for search, checks and price calculations. Keep/replacement entries need offer IDs and a reason so B can distinguish a retained catalog object from a repriced offer.
- **C request recovery:** send one `request_id` UUID per user mutation and reuse it only for byte-equivalent retries. On HTTP 409 `version_conflict`, reload `GET /api/sessions/{id}` and get user confirmation before applying their edit to the new version. On `request_in_progress`, poll `GET /requests/{request_id}`; on `completed`, reload session and trace. The endpoint does not replay prior SSE. A changed request body needs a fresh ID. An accepted request continues after client disconnect and commits atomically with messages/state/trace.
- **C:** consume `open_questions`, `advantages`, `tradeoffs`, `risks` and history; send commands through POST and parse SSE from `fetch`. For explicit agent stages, send `select_bundle` with `{bundle_id}` then `request_checkout` with `{}` and the latest `expected_version`; create the cart with `{session_id, version, bundle_id}` only after a non-blocking validation result. The cart and checkout endpoints independently enforce current version, offer availability/prices and blocking checks. Checkout remains C-owned. Keep one app worker because per-session serialization locks are process-local.
