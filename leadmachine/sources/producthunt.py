"""Product Hunt hardware launches (free developer token: PRODUCTHUNT_TOKEN)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

from .. import http
from ..config import Config, env
from ..models import Lead

API = "https://api.producthunt.com/v2/api/graphql"
TOPICS = ["hardware", "wearables", "home", "gadgets", "outdoors", "tech"]
GQL = """query($topic:String!,$after:DateTime!){posts(topic:$topic,postedAfter:$after,first:40,order:NEWEST){
 edges{node{name tagline description website url votesCount makers{name username}}}}}"""


def fetch(cfg: Config) -> Iterator[Lead]:
    token = env("PRODUCTHUNT_TOKEN")
    if not token:
        return
    after = (datetime.now(timezone.utc) - timedelta(days=int(cfg.source("producthunt").get("days_back", 7)))).isoformat()
    for topic in TOPICS:
        try:
            resp = http.SESSION.post(API, timeout=20, json={"query": GQL, "variables": {"topic": topic, "after": after}},
                                     headers={"Authorization": f"Bearer {token}"})
            edges = resp.json()["data"]["posts"]["edges"] if resp.ok else []
        except (ValueError, KeyError, TypeError, Exception):  # noqa: BLE001
            edges = []
        for e in edges:
            n = e.get("node") or {}
            makers = n.get("makers") or []
            yield Lead(source="producthunt", source_url=n.get("url", ""), name=n.get("name", ""),
                       stage="launch", contact_name=(makers[0].get("name") if makers else None),
                       description=f"{n.get('tagline', '')}. {n.get('description') or ''}"[:1000],
                       signals=["producthunt_launch"], extra={"redirect": n.get("website"), "topic": topic})
