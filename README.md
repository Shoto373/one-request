# Travel Agent — track B, sprint 1

Python 3.12 + FastAPI backend for the agent/state part. This repository currently has no track A catalog/search implementation and no track C UI, Docker Compose, or checkout implementation. The `Offer` contract in `app/catalog/models.py` and `catalog_entries` table are temporary, explicitly marked integration boundaries. The agent calls A only through `app.planning.build.build_bundles(requirements=..., state=...)`; with that module absent it returns an explicit integration error and invents no offers.

## Run

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set DATABASE_URL to a PostgreSQL 16 database and configure LLM_API_KEY.
set -a; . ./.env; set +a
uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1
```

Startup applies numbered SQL migrations once using `schema_migrations`, then loads any `data/seed/*.json` entries idempotently. It does not download catalog data. Migration/seed errors fail startup. `/health` works without an LLM key; session endpoints require PostgreSQL. Agent requests without `LLM_API_KEY` return an SSE `error` event with `llm_not_configured` and do not change the state. This backend is intentionally supported with one Uvicorn worker because the per-session request lock is process-local; SQL compare-and-swap still prevents a stale write.

## API contracts

- `GET /health` → `{"status":"ok"}`
- `POST /api/sessions` → `{"session_id":"<uuid>","version":0}`
- `GET /api/sessions/{id}` → `{"state": TripState, "messages": [...]}`
- `POST /api/sessions/{id}/messages` JSON: `{"text":"Из Москвы в Сочи 10–17 ноября, двое взрослых","expected_version":0}`. Response content type is `text/event-stream`.
- `GET /api/sessions/{id}/trace` → `{"session_id":"...","steps":[...]}`

SSE payloads are JSON, UTF-8, separated by a blank line:

```text
event: step\ndata: {"step_type":"llm","name":"parse_request","input":{...},"output":{...},"status":"ok"}

event: state\ndata: {"state":{"session_id":"...","version":1,"stage":"select",...}}

event: message\ndata: {"role":"assistant","text":"..."}

event: done\ndata: {"ok":true,"version":1}
```

`step` records actual parse/tool inputs and results, not model reasoning. A stream-time failure is sent as `event: error` with `{code,message}` followed by `event: done` with `{ok:false}`. The state event contains the exact committed state. A stale `expected_version` returns HTTP 409 before stream headers; an unknown session returns 404; malformed request bodies return 422.

The frontend uses POST, so consume with `fetch`, not `EventSource`:

```js
const response = await fetch(`/api/sessions/${sessionId}/messages`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ text: "Из Москвы в Сочи 10–17 ноября, двое взрослых", expected_version: 0 })
});
if (response.status === 409) { /* reload GET /api/sessions/{id} */ }
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
  -d '{"text":"Из Москвы в Сочи 10–17 ноября, двое взрослых","expected_version":0}'
curl -fsS "$BASE_URL/api/sessions/$SESSION_ID"
```

The last call returns the persisted state and messages. Until track A provides `app.planning.build.build_bundles`, the POST returns a structured `catalog_integration_missing` SSE error after parsing, and the state remains unchanged; after A is integrated the same example proceeds to save the resulting bundle and version 1.

## LLM

Configure `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_MODEL_FAST`, and optionally `LLM_TIMEOUT_SECONDS`. `chat`, `chat_json`, and `chat_tools` are asynchronous; transient timeouts/network/408/429/5xx responses have at most two retries. Invalid JSON/schema responses fail clearly without a repair loop. Logs include model, duration and provider-reported usage only; credentials are never logged. Manual smoke check: `python -m app.agent.smoke`.

## Tests

```sh
pytest -q
```

Unit tests use mocked LLM responses and in-memory test stores. PostgreSQL integration tests are skipped unless `TEST_DATABASE_URL` points to a dedicated test database. Never point it at the shared/development database. The real `build_bundles` test is skipped until track A's module exists. There is no checkout flow here; carts/orders are schema only and remain track C's responsibility.

## Integration handoff

- **A:** supply `app/catalog/models.py` Offer or map its existing Offer to this serialized v2 contract; provide `app.planning.build.build_bundles(requirements: dict, state: TripState) -> list[Bundle]`. Ensure prices/IDs/items come only from catalog tools. Replace `catalog_entries` with the owned catalog migrations and seed format.
- **C:** configure PostgreSQL and `DATABASE_URL`; use POST/fetch SSE schemas above; run one app worker; implement the UI, cart and checkout against `{session_id, version, bundle_id}`. Orders schema already has a unique idempotency key and immutable snapshot trigger, but no checkout behavior exists.
