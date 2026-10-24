"""Provider check for the configured OpenAI-compatible LLM (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL).

    python -m app.agent.smoke
    LLM_BASE_URL=https://openrouter.ai/api/v1 LLM_API_KEY=... LLM_MODEL=... python -m app.agent.smoke

Checks a plain chat answer, a JSON answer against a schema (the agent's main call) and prints which
provider capabilities were detected. Exit code 0 only if both calls work.
"""
import asyncio
import sys

from pydantic import BaseModel

from app.agent.llm import LLMClient


class SmokeJSON(BaseModel):
    city: str
    adults: int


async def main() -> int:
    client = LLMClient()
    print("provider:", client.describe())
    if not client.configured:
        print("FAIL: LLM is not configured (LLM_API_KEY / LLM_MODEL)")
        return 1
    try:
        answer = await client.chat([{"role": "user", "content": "Reply with exactly: LLM smoke test passed"}], fast=True)
        print("chat:", answer)
        parsed = await client.chat_json([
            {"role": "system", "content": 'Return JSON {"city": string, "adults": integer}.'},
            {"role": "user", "content": "Из Москвы, нас двое взрослых."},
        ], SmokeJSON, fast=True)
        print("chat_json:", parsed.model_dump())
    except Exception as exc:  # noqa: BLE001 - report any provider failure to the operator
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1
    print(f"capabilities: json_mode={client.json_mode} temperature={client.temperature_supported}")
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
