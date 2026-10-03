"""Hacker News stories about upcoming crowdfunding launches (free Algolia API, no key)."""
from __future__ import annotations

import time
from typing import Iterator

from .. import http
from ..config import Config
from ..models import Lead

API = "https://hn.algolia.com/api/v1/search_by_date"
QUERIES = ["kickstarter", "indiegogo", "crowdfunding", "pre-order", "preorder", "prototype hardware"]


def fetch(cfg: Config) -> Iterator[Lead]:
    days = int(cfg.source("hackernews").get("days_back", 14))
    since = int(time.time()) - days * 86400
    for q in QUERIES:
        data = http.get_json(API, params={"query": q, "tags": "story", "hitsPerPage": 50,
                                          "numericFilters": f"created_at_i>{since}"})
        for hit in (data or {}).get("hits", []):
            title = hit.get("title") or ""
            low = title.lower()
            if not ("show hn" in low or any(k in low for k in ("kickstarter", "indiegogo", "crowdfund", "pre-order", "preorder"))):
                continue
            yield Lead(source="hackernews", source_url=f"https://news.ycombinator.com/item?id={hit['objectID']}",
                       name=title.replace("Show HN:", "").strip(), stage="forum",
                       website=hit.get("url"), contact_name=hit.get("author"),
                       description=(hit.get("story_text") or "")[:1000],
                       signals=["hn_post"], extra={"points": hit.get("points")})
