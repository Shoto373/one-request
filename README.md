# О!дин запрос — AI-агент путешествий

> Проект создан на **хакатоне от О!стровка** (Ostrovok Hackathon 2026), трек «О!дин запрос».

Пользователь пишет одно сообщение своими словами («к тёплому морю в декабре, недорого, вдвоём, без долгих перелётов»), а агент доводит его до оформленной (имитированной) покупки поездки целиком. По пути агент:
- уточняет только недостающее;
- предлагает направления с погодой, ценой и временем в пути;
- находит реальные билеты и отели;
- сравнивает 3–4 комплекта и объясняет компромиссы и риски;
- проверяет состыковки;
- пересобирает план, когда условия меняются;
- помогает после покупки.

- **Работающий сервис:** https://3-pointers-team.online
- **Скринкаст:** https://drive.google.com/file/d/1BKAbopLIPQ6HgNral_39BjaP2zbvOQl-/view?usp=sharing
- **Хакатон:** Ostrovok Hackathon 2026 от О!стровка, трек «О!дин запрос»

## Быстрый старт

```bash
git clone https://github.com/Shoto373/5-ukazatelei.git
cd 5-ukazatelei
cp .env.example .env          # необязательно: без .env берутся значения из .env.example
docker compose up --build     # миграции и сид применяются автоматически, ~5 минут на первую сборку
# открыть http://localhost:8080
```

Больше ничего ставить не нужно. Ключ LLM для жюри лежит в зашифрованном `secrets.env.age`. Расшифруйте его в `.env` закрытым ключом организаторов: `age -d -i <key.txt> -o .env secrets.env.age`. Без ключа агент тоже проходит весь путь: запрос разбирают правила.

## Зависимости и переменные окружения

Рекомендуемые ресурсы:
- 2 CPU;
- 4 GB RAM;
- около 2 GB диска под образы.

Сервисы в `docker-compose.yml`:
- `app` — Python 3.12 + FastAPI + собранный React-фронт, порт 8080, один воркер, не от root;
- `db` — PostgreSQL 16 (`postgres:16.6-alpine`), данные в volume `pgdata`.

Кэша и очередей нет. Сетевой доступ нужен к Tutu MCP (без ключа) и к провайдеру LLM.

**LLM.** Используется GigaChat (Сбер), модель `GigaChat-Max`, через OpenAI-совместимый API (`LLM_BASE_URL=https://gigachat.devices.sberbank.ru/api/v1`). Подойдёт любой OpenAI-совместимый провайдер: OpenAI, OpenRouter, DeepSeek, локальная Ollama.

Все переменные с комментариями перечислены в [`.env.example`](.env.example). Compose сначала читает `.env.example` (рабочие значения по умолчанию), затем `.env`, если он есть.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `APP_PORT` | `8080` | порт приложения в контейнере |
| `DATABASE_URL` | `postgresql://app:app@db:5432/trip` | подключение к Postgres |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `app` / `app` / `trip` | инициализация Postgres |
| `TRAVEL_DATA_PROVIDER` | `tutu` | `tutu` — живой поиск билетов и отелей, `demo` — офлайн-каталог без сети (для тестов) |
| `LLM_BASE_URL` / `LLM_API_KEY` | OpenAI / пусто | OpenAI-совместимый API; без ключа работает разбор правилами |
| `LLM_MODEL` / `LLM_MODEL_FAST` | `gpt-4o-mini` | основная и быстрая модели |
| `LLM_TIMEOUT_SECONDS` | `30` | таймаут запроса к LLM |
| `LLM_JSON_MODE` | `auto` | JSON-режим, если провайдер его поддерживает |
| `TUTU_MCP_ENDPOINT` | `https://mcp.tutu.ru/mcp` | официальный endpoint Tutu MCP |
| `TUTU_TIMEOUT_SECONDS` | `25` | таймаут вызова MCP |
| `TUTU_MAX_CALLS` / `TUTU_MAX_PARALLEL` | `18` / `2` | наши защитные лимиты на один поиск |
| `TUTU_CACHE` | `off` | `pinned` — первый ответ Туту на запрос записывается в Postgres и повторяется для тех же параметров (с исходным временем получения): так демонстрация показывает одни и те же реальные цены на каждом шаге. Включён на развёрнутом сервисе |
| `LOG_LEVEL` | `INFO` | уровень логов (stdout, `docker compose logs app`) |

## Сидирование

Делать ничего не нужно. При старте `app`:
1. применяются миграции `db/migrations/*.sql`;
2. загружается сид `data/seed/*.json`;
3. каталог (`data/seed/catalog/*.json`: города, аэропорты, климат, визы) читается в память;
4. в фоне заполняется таблица источников для сводки «откуда данные».

Повторный запуск ничего не дублирует. Начать с чистой БД: `make reset && make up` или `docker compose down -v && docker compose up --build`. Пересборка каталога из снимков открытых данных описана в [`pipeline/README.md`](pipeline/README.md).

## Маршруты и доступ

- `/` — чат с агентом и «холст поездки»: условия, направления, сравнение комплектов, таймлайн с проверками;
- `/checkout/:cartId` — оформление (имитация оплаты);
- `/orders/:id` — заказ: неизменяемый снимок, напоминания, ранний заезд, перенос дат;
- `/metrics` — метрики по фиксированным сценариям, `/api/metrics` — то же в JSON;
- `/health` — `200 {"status":"ok"}`; `/api/status` — режим LLM и данных;
- `/api/*` — REST и SSE, контракт в разделе [Для разработчиков](#для-разработчиков).

Учётных записей нет: сессия создаётся автоматически, кнопка «Новый чат» начинает новую. На оплате подходят любые имя и email, деньги не списываются.

## Как агент закрывает задачи кейса

| Задача кейса | Что делает агент | Где в коде |
|---|---|---|
| Понимание запроса и уточнение | Выделяет явные и скрытые требования: бюджет, состав, животное, класс перелёта, питание, «без долгих перелётов». Свои догадки помечает отдельно, и их можно убрать одним кликом. Спрашивает только то, без чего поиск невозможен, не больше двух вопросов за ход, с готовыми вариантами ответа. Короткие ответы («Эконом», «8 кг», «Москва») относит к заданному вопросу. | `app/agent/orchestrator.py` (`parse_request`), `app/agent/requirements.py`, `app/agent/rule_parser.py` |
| Вдохновение и выбор направления | Если направление не названо, предлагает до 4 направлений, где поездка реально собирается. У каждого: климат за месяц (воздух, море, дожди), ориентировочная цена всей поездки, время в пути и визовый режим. Если ничего не подходит, называет минимальные изменения условий, а не выдумывает. | `app/planning/destinations.py`, `app/planning/suggest.py`, `app/planning/alternatives.py` |
| Подбор и сравнение | Живой поиск: перелёты (с классом обслуживания, отдельно туда и обратно), поезда и отели через официальный Tutu MCP. Комплекты «дешевле», «быстрее», «отель выше классом», «другой отель» сравниваются по цене, времени в пути, пересадкам, багажу, условиям отмены, питанию и стыковкам по времени. | `app/providers/tutu/planner.py`, `app/providers/tutu/adapter.py` |
| Объяснение выбора | У каждого комплекта есть плюсы, компромиссы и риски. Каждое утверждение ссылается на конкретное предложение или проверку: фразы LLM без такой опоры отбрасываются (`_explain_bundle`). | `app/agent/explain.py`, `app/agent/orchestrator.py` |
| Сборка поездки и покупка | Таймлайн по порядку: дорога туда → трансфер → отель → трансфер → обратно. Проверки: прибытие и время заезда, круглосуточная стойка, выезд и обратная дорога, вместимость, питомец, бюджет. Перед оплатой каждое предложение ищется заново и сверяется по номеру рейса, времени, номеру в отеле и цене. Корзина и заказ хранят неизменяемый снимок, повторная оплата не создаёт второй заказ. | `app/providers/tutu/revalidation.py`, `app/cart/` |
| Пересборка плана | «А если на два дня позже?», «бюджет меньше», «с нами едет собака»: агент меняет только затронутые условия, повторяет поиск и показывает, что сохранилось, что заменилось и насколько изменилась цена. | `app/agent/orchestrator.py` (`_replan`), `app/planning/replan.py` |
| Работа после покупки | Напоминания (онлайн-регистрация, вылет, заселение, крайний срок бесплатной отмены), запрос раннего заезда, расчёт и подтверждение переноса дат. | `app/cart/aftercare.py` |
| Память | Профиль в браузере: в следующей поездке агент помнит город вылета, состав и питание как догадки, которые можно убрать. | `app/profile.py` |
| Метрики | Фиксированные сценарии с независимыми проверками: доля доведённых до конца сценариев, число уточнений, ошибки состыковок. Страница `/metrics`. | `eval/`, `app/metrics/` |

## Откуда данные (честно)

| Данные | Источник | Реальные? |
|---|---|---|
| Авиабилеты (цена на весь состав, класс, пересадки, багаж) | официальный [Tutu MCP](https://mcp.tutu.ru/mcp), `search_avia` | да, живой поиск |
| Поезда | Tutu MCP, `search_rail` | да; сумма = цена места × состав, поэтому помечается предупреждением |
| Отели (цена за весь срок, номер, питание, отмена) | Tutu MCP, `search_hotels` + `get_offer_details`; Туту продаёт отели из базы Островка | да. Ссылка ведёт на страницу этого отеля на Островке, если Островок его узнал, иначе на это же предложение на Туту. Цена на сайте Островка может отличаться от цены Туту |
| Трансфер до отеля | оценка агента по расстоянию аэропорт/вокзал → центр | **нет**: подписан «только предложение», без ссылки, не бронируется |
| Климат по месяцам | Open-Meteo, статистика 2021–2025 (средние, не прогноз) | да |
| Города, аэропорты, визы для граждан РФ | Wikidata, OurAirports, справочник виз | да |
| Ориентировочная цена направления на шаге выбора | демо-каталог (`data/seed/catalog`, `seed = 42`) | **нет**: показывается как «≈ примерно»; точные цены появляются после выбора направления |
| Оплата и бронирование | — | **имитация**: деньги не списываются, бронь не создаётся |

Если живой поиск не нашёл маршрут или не ответил, агент не подставляет выдуманные цены. Он говорит об этом прямо или задаёт вопрос, например: «Из Рязани нет рейсов — искать вылет из Москвы?».

Из инструментов Tutu MCP агенту разрешены только читающие (`app/providers/tutu/client.py`, allowlist). Регистрация пассажиров, создание заказа и оплата у поставщика не вызываются.

Атрибуция: © участники OpenStreetMap (ODbL); Weather data by Open-Meteo.com (CC BY 4.0); Wikidata (CC0); OurAirports (public domain).

## Архитектура

```
React (web/)  ──POST /api/sessions/{id}/messages──►  FastAPI (app/main.py)  ──►  Postgres
   ▲  SSE: step → state → message → done              │  блокировка сессии, idempotency по request_id,
   │                                                   │  optimistic concurrency по version
   └───────────────────────────────────────────────────┤
                                                       ▼
                                   Orchestrator.prepare_turn (app/agent/orchestrator.py)
                                   1. parse_request — LLM (быстрая модель) → изменения требований в JSON;
                                      при сбое LLM — разбор правилами (rule_parser.py)
                                   2. validate_extraction — только известные ключи, цитаты из текста
                                   3. merge + сборка дат, догадки агента помечены отдельно
                                   4. нет направления → suggest_destinations
                                      есть направление → build_bundles (живой поиск Туту)
                                      был выбран комплект и условия изменились → replan
                                   5. объяснения: LLM формулирует, код оставляет только подтверждённое
                                   6. атомарное сохранение: состояние + сообщения + трейс шагов
```

Принципы:
- **Цифры считает код, а не LLM.** Модель извлекает требования и формулирует объяснения. Цены, суммы, стыковки и проверки вычисляются детерминированно и сверяются (сумма позиций = итог комплекта).
- **Работает без ключа LLM.** Если ключа нет или провайдер не ответил, запрос разбирают правила, и весь путь до оплаты проходит.
- **Прозрачность.** Каждый шаг агента (разбор, вызовы инструментов, результаты) сохраняется и виден в интерфейсе («Шаги агента») и через `GET /api/sessions/{id}/trace`.
- **Надёжность.** У каждого изменения есть `request_id` (повтор не применяется дважды). Устаревшая версия плана даёт 409. Обрыв соединения не теряет уже принятый ход.

## Как проверить за 5 минут

1. «Хочу к тёплому морю в декабре на неделю, недорого, вдвоём, без долгих перелётов». Агент спросит город вылета.
2. «Из Рязани, 6–13 декабря». Появятся направления с погодой, ценой и временем в пути.
3. Выберите Шарм-эль-Шейх. Агент спросит класс перелёта, ответьте «Эконом». В Рязани нет аэропорта, агент предложит Москву, ответьте «Москва». Появятся комплекты с реальными рейсами и отелями.
4. «А если на два дня позже?» Агент покажет, что сохранилось, что заменилось и насколько изменилась цена.
5. Выберите комплект → «Оформить». Пройдёт перепроверка цен, имитация оплаты, затем откроется заказ с напоминаниями и ранним заездом.

Другие запросы:
- «Из Москвы в Париж 6–8 декабря вдвоём, бизнес туда, эконом обратно»;
- «Из Москвы в Санкт-Петербург на поезде 14–16 ноября»;
- «Неделя в Дубае на троих за 30 000» — честный ответ про невыполнимый бюджет;
- английский: «Somewhere warm in December for two».

## Тесты и метрики

```bash
make test        # pytest в тестовом образе; интеграционные — на отдельной БД trip_test
make eval        # фиксированные сценарии через настоящий подбор без LLM + независимые проверки
make eval-e2e    # сквозной прогон по запущенному API с реальным LLM, отчёт в eval/reports/
make rehearsal   # 10 «чужих» запросов из eval/rehearsal.txt — агент не должен ломаться
```

Тесты покрывают:
- API и SSE-контракт;
- корзину и оплату;
- ход агента;
- разбор запроса правилами;
- совместимость с разными LLM-провайдерами;
- сборку комплектов и перепроверку перед оплатой;
- живой поиск Туту.

Метрики по сценариям: страница `/metrics` и `GET /api/metrics`.

## Деплой

На сервере работает pull-CI/CD: `deploy/cd.sh`, его запускает systemd-таймер каждые 2 минуты. Новый коммит в `main` проходит тесты, затем собирается образ, выкатывается и проходит проверку здоровья. Если проверка не прошла, разворачивается предыдущая версия. HTTPS даёт nginx с сертификатом Let's Encrypt. Подробности: [`deploy/README.md`](deploy/README.md).

## Ограничения

- Город отправления — российский: так покрывает поиск Туту. Направления — по всему миру.
- Цена отеля — цена продажи на Туту, на сайте Островка она может отличаться: партнёрского API Островка у команды нет.
- Трансфер — ориентировочная оценка, не бронируется.
- Ориентировочная цена направления на шаге выбора берётся из демо-каталога. Реальные цены появляются после выбора направления.
- Оплата и бронирование имитируются.

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

The last call returns the persisted state and messages. The replan input contains the whole current requirement set, original selected bundle and accumulated diff since it was selected. Climate refs such as `climate:AYT:11.sea_temp` resolve to `{value,label,unit,kind,source,source_refs}` in the destination `sources` map for UI tooltips. Monthly climate values are statistics, not forecasts.

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
