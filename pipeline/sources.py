"""Network fetchers for real data with an on-disk cache and retries.

Only ``python -m pipeline.build --refresh`` touches the network. Raw responses go to
``pipeline/.cache`` (gitignored); compact extracts go to ``pipeline/snapshots`` (committed),
so generation is deterministic and offline by default.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).parent / ".cache"
USER_AGENT = "ostrovok-hackathon-2026-5-ukazatelei/0.1 (catalog pipeline)"

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
OURAIRPORTS_CSV = "https://davidmegginson.github.io/ourairports-data/airports.csv"
# HTTPS to overpass-api.de is reset on some networks; plain HTTP works.
OVERPASS_URLS = [
    "http://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_MARINE = "https://marine-api.open-meteo.com/v1/marine"


def _cache_path(key: str, suffix: str) -> Path:
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{digest}{suffix}"


def http_get(url: str, *, data: dict[str, str] | None = None, timeout: int = 180, attempts: int = 6) -> bytes:
    """GET (or form POST when ``data`` given) with cache and exponential backoff."""
    key = url + ("?" + urllib.parse.urlencode(data) if data else "")
    path = _cache_path(key, ".bin")
    if path.exists():
        return path.read_bytes()
    body = urllib.parse.urlencode(data).encode() if data else None
    delay = 5.0
    last: Exception | None = None
    for _ in range(attempts):
        req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            return payload
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"fetch failed: {url}: {last}")


def http_json(url: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    return json.loads(http_get(url, **kw))


def wikidata_by_enwiki(titles: list[str]) -> dict[str, dict[str, Any]]:
    """enwiki title -> Wikidata entity (labels/descriptions ru+en, claims, sitelinks)."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(titles), 40):
        chunk = titles[i : i + 40]
        res = http_json(
            WIKIDATA_API,
            {
                "action": "wbgetentities",
                "sites": "enwiki",
                "titles": "|".join(chunk),
                "props": "labels|descriptions|claims|sitelinks",
                "languages": "ru|en",
                "sitefilter": "enwiki|ruwiki",
                "format": "json",
            },
        )
        for ent in res["entities"].values():
            if "missing" in ent:
                continue
            title = ent["sitelinks"]["enwiki"]["title"]
            out[title] = ent
    return out


def overpass(query: str) -> dict[str, Any]:
    last: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            raw = http_get(url, data={"data": query}, attempts=4)
            res = json.loads(raw)
            if "elements" in res:
                return res
            last = RuntimeError(str(res)[:300])
        except (RuntimeError, json.JSONDecodeError) as exc:
            last = exc
            # A non-JSON body (busy page) may have been cached; drop it before the next mirror.
            _cache_path(url + "?" + urllib.parse.urlencode({"data": query}), ".bin").unlink(missing_ok=True)
    raise RuntimeError(f"overpass failed: {last}")
