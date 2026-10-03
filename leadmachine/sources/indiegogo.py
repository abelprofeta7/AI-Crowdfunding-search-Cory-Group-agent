"""Indiegogo pre-launch ("coming soon") pages, found through web search."""
from __future__ import annotations

import re
from typing import Iterator

from .. import websearch
from ..config import Config
from ..models import Lead

QUERIES = [
    'site:indiegogo.com/projects "coming soon"',
    'site:indiegogo.com/projects "launching soon" "get notified"',
    'site:indiegogo.com/projects "coming soon" gadget',
    'site:indiegogo.com/projects "coming soon" outdoor',
    'site:indiegogo.com/projects "coming soon" wearable',
    'site:indiegogo.com/projects "coming soon" "e-bike" OR scooter',
    'site:indiegogo.com/projects "coming soon" camera',
    'site:indiegogo.com/projects "coming soon" kitchen',
]
_RE = re.compile(r"https?://(?:www\.)?indiegogo\.com/projects/[^/?#]+")


def fetch(cfg: Config) -> Iterator[Lead]:
    if not websearch.available():
        return
    n = int(cfg.source("indiegogo").get("queries_per_run", 4))
    for q in websearch.rotate(QUERIES, n):
        for r in websearch.search(q):
            m = _RE.match(r.get("url") or "")
            if not m:
                continue
            title = re.sub(r"\s*[|-]\s*Indiegogo.*$", "", r.get("title", "")).strip()
            yield Lead(source="indiegogo", source_url=m.group(0), name=title or m.group(0),
                       stage="prelaunch", description=r.get("snippet"),
                       signals=["indiegogo_page"], extra={"platform": "indiegogo"})
