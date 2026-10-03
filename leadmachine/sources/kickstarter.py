"""Kickstarter "upcoming" (pre-launch / Coming Soon) projects.

A creator with a live pre-launch page is typically 60-90 days from launch and
in "list-building panic" mode: the exact moment they need an agency.
Primary path: Kickstarter's discover JSON. Fallback: web search for
pre-launch pages (Kickstarter often blocks datacenter IPs).
"""
from __future__ import annotations

import logging
import re
from typing import Iterator

from .. import http, websearch
from ..config import Config
from ..models import Lead

log = logging.getLogger(__name__)

DISCOVER = "https://www.kickstarter.com/discover/advanced"
FALLBACK_QUERIES = [
    'site:kickstarter.com/projects "coming soon" "notify me on launch"',
    'site:kickstarter.com/projects "launching soon" gadget',
    'site:kickstarter.com/projects "coming soon" "everyday carry"',
    'site:kickstarter.com/projects "coming soon" tabletop game',
    'site:kickstarter.com/projects "coming soon" desk setup',
    'site:kickstarter.com/projects "coming soon" outdoor gear',
    'site:kickstarter.com/projects "coming soon" 3d printer',
    'site:kickstarter.com/projects "coming soon" smart home',
]


def parse_project(p: dict) -> Lead | None:
    urls = (p.get("urls") or {}).get("web") or {}
    url = urls.get("project")
    if not url:
        return None
    creator = p.get("creator") or {}
    cat = p.get("category") or {}
    goal = p.get("goal")
    return Lead(
        source="kickstarter_upcoming", source_url=url.split("?")[0], name=p.get("name", "").strip(),
        stage="prelaunch", contact_name=creator.get("name"),
        category=" / ".join(x for x in (cat.get("parent_name"), cat.get("name")) if x) or None,
        location=(p.get("location") or {}).get("displayable_name"),
        description=p.get("blurb"),
        signals=["kickstarter_prelaunch_page"],
        extra={"goal": goal, "currency": p.get("currency"), "country": p.get("country"),
               "creator_url": ((creator.get("urls") or {}).get("web") or {}).get("user"),
               "platform": "kickstarter"},
    )


def _discover(cfg: Config) -> Iterator[Lead]:
    src = cfg.source("kickstarter_upcoming")
    headers = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest",
               "User-Agent": http.BROWSER_UA}
    for cid in src.get("category_ids", [16]):
        for page in range(1, int(src.get("pages_per_category", 2)) + 1):
            data = http.get_json(DISCOVER, headers=headers, params={
                "state": "upcoming", "category_id": cid, "sort": "newest", "format": "json", "page": page})
            if not data:
                return  # blocked or empty: let the caller try the fallback
            for p in data.get("projects", []):
                lead = parse_project(p)
                if lead:
                    yield lead
            if not data.get("has_more", True):
                break
            http.pause(1.5)


_PROJECT_RE = re.compile(r"https?://(?:www\.)?kickstarter\.com/projects/[^/?#]+/[^/?#]+")


def _fallback(cfg: Config) -> Iterator[Lead]:
    if not websearch.available():
        return
    for q in websearch.rotate(FALLBACK_QUERIES, 3):
        for r in websearch.search(q):
            m = _PROJECT_RE.match(r.get("url") or "")
            if not m:
                continue
            title = re.sub(r"\s+by\s+.+$|\s*[-|]\s*Kickstarter.*$", "", r.get("title", "")).strip()
            yield Lead(source="kickstarter_upcoming", source_url=m.group(0), name=title or m.group(0),
                       stage="prelaunch", description=r.get("snippet"),
                       signals=["kickstarter_prelaunch_page"], extra={"platform": "kickstarter"})


def fetch(cfg: Config) -> Iterator[Lead]:
    got = 0
    for lead in _discover(cfg):
        got += 1
        yield lead
    if got == 0:
        log.info("Kickstarter discover returned nothing; using web-search fallback")
        yield from _fallback(cfg)
