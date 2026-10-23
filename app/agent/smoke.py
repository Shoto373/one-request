"""Manual LLM smoke test. Requires LLM_API_KEY and configured model/base URL."""
import asyncio

from app.agent.llm import LLMClient


async def main() -> None:
    client = LLMClient()
    answer = await client.chat([{"role": "user", "content": "Reply with exactly: LLM smoke test passed"}], fast=True)
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())
