"""Reddit posts from creators preparing a crowdfunding launch.

Strategy rule: never pitch in public threads. These leads go to the manual
engagement queue with a drafted helpful reply; if the post links to the
product's own site, enrichment may also find an email for direct outreach.
Uses the official OAuth API when REDDIT_CLIENT_ID/SECRET are set (recommended;
anonymous JSON is often blocked from cloud IPs).
"""
from __future__ import annotations

import re
import time
from typing import Iterator

from .. import http
from ..config import Config, env
from ..models import Lead, domain_of, is_platform

QUERY = '"kickstarter" OR "indiegogo" OR "pre-launch" OR "prelaunch" OR "crowdfunding campaign" OR "launching soon"'
_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")


def _token() -> str | None:
    cid, secret = env("REDDIT_CLIENT_ID"), env("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        return None
    try:
        resp = http.SESSION.post("https://www.reddit.com/api/v1/access_token", timeout=15,
                                 auth=(cid, secret), data={"grant_type": "client_credentials"})
        return resp.json().get("access_token") if resp.ok else None
    except Exception:  # noqa: BLE001
        return None


def parse_post(d: dict) -> Lead:
    text = f"{d.get('selftext') or ''} {d.get('url') or ''}"
    urls = [u.rstrip(".,") for u in _URL_RE.findall(text)]
    own = next((u for u in urls if not is_platform(domain_of(u))), None)
    crowd = [u for u in urls if "kickstarter.com" in u or "indiegogo.com" in u]
    return Lead(source="reddit", source_url="https://www.reddit.com" + d.get("permalink", ""),
                name=(d.get("title") or "").strip(), stage="forum", website=own,
                contact_name=f"u/{d.get('author')}", description=(d.get("selftext") or "")[:1500],
                signals=["reddit_post"], extra={"subreddit": d.get("subreddit"), "crowdfunding_links": crowd,
                                                "score": d.get("score")})


def fetch(cfg: Config) -> Iterator[Lead]:
    src = cfg.source("reddit")
    since = time.time() - int(src.get("days_back", 7)) * 86400
    token = _token()
    base = "https://oauth.reddit.com" if token else "https://www.reddit.com"
    headers = {"User-Agent": "python:corygroup-leadmachine:1.0 (by /u/corygroup)"}
    if token:
        headers["Authorization"] = f"bearer {token}"
    for sub in src.get("subreddits", []):
        path = f"/r/{sub}/search" + ("" if token else ".json")
        data = http.get_json(base + path, headers=headers, params={
            "q": QUERY, "restrict_sr": 1, "sort": "new", "t": "week", "limit": 50})
        for child in ((data or {}).get("data") or {}).get("children", []):
            d = child.get("data") or {}
            if d.get("created_utc", 0) < since or d.get("stickied"):
                continue
            yield parse_post(d)
        http.pause(1.0)
