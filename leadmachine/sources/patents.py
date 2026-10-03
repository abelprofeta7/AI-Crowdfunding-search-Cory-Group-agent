"""Recently granted US design patents for consumer products (PatentsView API).

Design patents land roughly 3-6 months before a product is revealed, right
when founders are planning the first production run. Needs a free key:
PATENTSVIEW_API_KEY. The assignee company becomes the lead; enrichment finds
its website and a contact.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterator

from .. import http
from ..config import Config, env
from ..models import Lead

log = logging.getLogger(__name__)

DEFAULT_URL = "https://search.patentsview.org/api/v1/patent/"
# Large manufacturers that will never hire a crowdfunding agency.
BIG_ASSIGNEES = ("apple", "samsung", "google", "microsoft", "amazon", "sony", "lg electronics", "nike",
                 "adidas", "procter", "unilever", "honda", "toyota", "ford", "general motors", "boeing",
                 "philips", "panasonic", "huawei", "xiaomi", "dyson", "whirlpool", "3m", "johnson",
                 "stanley black", "milwaukee", "techtronic", "lenovo", "dell", "hp ", "hewlett", "canon",
                 "nintendo", "meta platforms", "intel", "qualcomm", "bosch", "siemens", "yeti")
CONSUMER_WORDS = ("device", "charger", "speaker", "light", "lamp", "bag", "case", "bottle", "tool", "knife",
                  "toy", "game", "holder", "stand", "mount", "wallet", "headphone", "earphone", "camera",
                  "drone", "robot", "bike", "bicycle", "scooter", "printer", "keyboard", "watch", "tracker",
                  "grill", "cooler", "tent", "backpack", "controller", "display", "clip", "carabiner")


def fetch(cfg: Config) -> Iterator[Lead]:
    key = env("PATENTSVIEW_API_KEY")
    if not key:
        return
    src = cfg.source("patents")
    since = (date.today() - timedelta(days=int(src.get("days_back", 90)))).isoformat()
    body = {
        "q": {"_and": [{"_gte": {"patent_date": since}}, {"patent_type": "design"},
                       {"assignees.assignee_country": "US"}]},
        "f": ["patent_id", "patent_title", "patent_date", "assignees.assignee_organization",
              "assignees.assignee_city", "assignees.assignee_state",
              "inventors.inventor_name_first", "inventors.inventor_name_last"],
        "s": [{"patent_date": "desc"}],
        "o": {"size": int(src.get("max_results", 100))},
    }
    try:
        resp = http.SESSION.post(env("PATENTSVIEW_URL", DEFAULT_URL), json=body, timeout=30,
                                 headers={"X-Api-Key": key, "Accept": "application/json"})
        patents = resp.json().get("patents", []) if resp.ok else []
        if not resp.ok:
            log.warning("PatentsView HTTP %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:  # noqa: BLE001
        log.warning("PatentsView failed: %s", exc)
        return
    for p in patents:
        title = (p.get("patent_title") or "").strip()
        orgs = [a for a in (p.get("assignees") or []) if a.get("assignee_organization")]
        if not orgs or not any(w in title.lower() for w in CONSUMER_WORDS):
            continue
        org = orgs[0]["assignee_organization"]
        if any(b in org.lower() for b in BIG_ASSIGNEES):
            continue
        inv = (p.get("inventors") or [{}])[0]
        inventor = " ".join(x for x in (inv.get("inventor_name_first"), inv.get("inventor_name_last")) if x)
        pid = p.get("patent_id", "")
        yield Lead(source="patents", source_url=f"https://patents.google.com/patent/US{pid}",
                   name=title.title(), company=org, stage="patent", contact_name=inventor or None,
                   contact_role="Inventor", location=", ".join(x for x in (orgs[0].get("assignee_city"),
                                                                           orgs[0].get("assignee_state")) if x),
                   signals=["design_patent_granted"], extra={"patent_id": pid, "patent_date": p.get("patent_date")})
