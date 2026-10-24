"""Репетиция защиты: прогнать «чужие» запросы через запущенный API и убедиться, что агент не ломается.

    python -m eval.rehearsal [--base-url http://localhost:8080] [--file eval/rehearsal.txt]

Каждый запрос — новая сессия и одно сообщение, без скриптовых ответов (как вопрос жюри).
Успех хода: SSE закончился done ok, агент ответил текстом, а состояние сохранилось (версия выросла).
Что получилось — уточнение, направления, комплекты или честный отказ — печатается для ручной проверки.
Отчёт пишется в eval/reports/rehearsal-<время>.json.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx


def ask(client: httpx.Client, text: str) -> dict:
    session = client.post("/api/sessions", json={}).json()
    sid = session["session_id"]
    started = time.monotonic()
    events: list[tuple[str, dict]] = []
    body = {"request_id": str(uuid4()), "text": text, "expected_version": session["version"]}
    with client.stream("POST", f"/api/sessions/{sid}/messages", json=body) as res:
        if res.status_code != 200:
            return {"text": text, "ok": False, "error": f"HTTP {res.status_code}: {res.read().decode()[:200]}"}
        event = "message"
        for line in res.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                events.append((event, json.loads(line[5:].strip())))
    seconds = round(time.monotonic() - started, 1)
    state = client.get(f"/api/sessions/{sid}").json()["state"]
    error = next((d for e, d in events if e == "error"), None)
    done = next((d for e, d in events if e == "done"), None)
    reply = next((d.get("text", "") for e, d in events if e == "message"), "")
    return {
        "text": text,
        "ok": bool(done and done.get("ok")) and not error and bool(reply.strip()) and state["version"] > 0,
        "seconds": seconds,
        "error": error,
        "stage": state["stage"],
        "questions": [q.get("question") for q in state.get("open_questions", [])],
        "destinations": [d.get("name") for d in state.get("destinations", [])],
        "bundles": [{"label": b.get("label"), "total_rub": b.get("total_rub")} for b in state.get("bundles", [])],
        "proposed_changes": [c.get("label") for c in state.get("proposed_changes") or []],
        "reply": reply,
        "session_id": sid,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8080")
    p.add_argument("--file", default=str(Path(__file__).with_name("rehearsal.txt")))
    p.add_argument("--timeout", type=float, default=300)
    args = p.parse_args()
    queries = [l.strip() for l in Path(args.file).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]

    results = []
    with httpx.Client(base_url=args.base_url, timeout=args.timeout) as client:
        for i, q in enumerate(queries, 1):
            try:
                r = ask(client, q)
            except Exception as exc:  # сеть, таймаут — тоже провал репетиции
                r = {"text": q, "ok": False, "error": repr(exc)}
            results.append(r)
            outcome = (
                f"{len(r.get('bundles', []))} компл." if r.get("bundles")
                else f"{len(r['destinations'])} направл." if r.get("destinations")
                else "варианты изменений" if r.get("proposed_changes")
                else "уточняет" if r.get("questions")
                else r.get("stage", "—")
            )
            print(f"{'OK ' if r['ok'] else 'FAIL'} {i:>2}. [{r.get('seconds', '—')} с] {outcome:<18} {q}")
            if not r["ok"]:
                print(f"       ошибка: {r.get('error')}")
            elif r.get("reply"):
                print(f"       агент: {r['reply'][:160].replace(chr(10), ' ')}")

    passed = sum(r["ok"] for r in results)
    print(f"\nне сломался: {passed}/{len(results)}")
    out = Path(__file__).with_name("reports") / f"rehearsal-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"base_url": args.base_url, "passed": passed, "total": len(results), "results": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"отчёт: {out}")


if __name__ == "__main__":
    main()
