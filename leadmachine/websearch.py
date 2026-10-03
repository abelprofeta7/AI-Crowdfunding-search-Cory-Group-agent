"""Pluggable web search. Uses the first provider that has credentials configured.

Free / near-free options (pick one, see docs/SETUP.md):
  SEARXNG_URL          self-hosted SearXNG instance (free, unlimited)
  BRAVE_API_KEY        Brave Search API (monthly free credit)
  SERPER_API_KEY       serper.dev (2,500 free queries on sign-up)
  GOOGLE_CSE_KEY + GOOGLE_CSE_CX   Google Programmable Search (100 free/day)
"""
from __future__ import annotations

import logging
from datetime import date

from . import http
from .config import env

log = logging.getLogger(__name__)


def provider() -> str | None:
    for name, keys in (("searxng", ["SEARXNG_URL"]), ("brave", ["BRAVE_API_KEY"]),
                       ("serper", ["SERPER_API_KEY"]), ("google", ["GOOGLE_CSE_KEY", "GOOGLE_CSE_CX"])):
        if all(env(k) for k in keys):
            return name
    return None


def available() -> bool:
    return provider() is not None


def search(query: str, n: int = 20) -> list[dict]:
    """Returns [{url, title, snippet}]; [] when no provider is configured or on error."""
    p = provider()
    try:
        if p == "searxng":
            data = http.get_json(env("SEARXNG_URL").rstrip("/") + "/search",
                                 params={"q": query, "format": "json"})
            items = (data or {}).get("results", [])
            return [{"url": i.get("url"), "title": i.get("title", ""), "snippet": i.get("content", "")}
                    for i in items[:n]]
        if p == "brave":
            data = http.get_json("https://api.search.brave.com/res/v1/web/search",
                                 params={"q": query, "count": min(n, 20)},
                                 headers={"X-Subscription-Token": env("BRAVE_API_KEY"),
                                          "Accept": "application/json"})
            items = ((data or {}).get("web") or {}).get("results", [])
            return [{"url": i.get("url"), "title": i.get("title", ""), "snippet": i.get("description", "")}
                    for i in items]
        if p == "serper":
            resp = http.SESSION.post("https://google.serper.dev/search", timeout=20,
                                     json={"q": query, "num": n},
                                     headers={"X-API-KEY": env("SERPER_API_KEY")})
            items = resp.json().get("organic", []) if resp.ok else []
            return [{"url": i.get("link"), "title": i.get("title", ""), "snippet": i.get("snippet", "")}
                    for i in items]
        if p == "google":
            data = http.get_json("https://www.googleapis.com/customsearch/v1",
                                 params={"key": env("GOOGLE_CSE_KEY"), "cx": env("GOOGLE_CSE_CX"),
                                         "q": query, "num": min(n, 10)})
            items = (data or {}).get("items", [])
            return [{"url": i.get("link"), "title": i.get("title", ""), "snippet": i.get("snippet", "")}
                    for i in items]
    except Exception as exc:  # noqa: BLE001 - a search failure must never stop the run
        log.warning("search (%s) failed for %r: %s", p, query, exc)
    return []


def rotate(items: list, count: int, salt: int = 0) -> list:
    """Pick `count` items, starting at an offset that moves every day, so
    consecutive runs cover different queries without repeating."""
    if not items:
        return []
    start = (date.today().toordinal() * max(count, 1) + salt) % len(items)
    return [items[(start + i) % len(items)] for i in range(min(count, len(items)))]
