"""E-commerce stores (Shopify / WooCommerce) selling unreleased products on pre-order.

These brands have validated demand but are leaking revenue on a static
pre-order page; the pitch is to turn the next batch into a crowdfunding launch.
Discovery = web search; the enrichment agent then confirms the pre-order app
fingerprint on the live site and disqualifies false positives.
"""
from __future__ import annotations

from typing import Iterator

from .. import websearch
from ..config import Config
from ..models import Lead, domain_of, is_platform

CATEGORIES = ["gadget", "EDC", "desk accessory", "outdoor gear", "camping", "tabletop game",
              "3D printer", "e-bike", "smart home", "headphones", "camera gear", "fitness equipment",
              "multi-tool", "backpack", "wearable", "charger", "keyboard", "drone", "e-ink", "robot"]
TEMPLATES = [
    '"pre-order" "ships in" {c} -amazon -ebay -walmart',
    '"preorder now" "estimated delivery" {c}',
    '"reserve yours" "pre-order" {c} -amazon',
    '"powered by PreProduct" {c}',
    '"Purple Dot" pre-order {c}',
    'inurl:products "pre-order" "expected to ship" {c}',
    '"first batch" "pre-order" {c} -kickstarter -indiegogo',
]
_SKIP = ("reddit.", "youtube.", "amazon.", "ebay.", "walmart.", "bestbuy.", "target.com", "aliexpress.",
         "pinterest.", "quora.", "forbes.", "theverge.", "engadget.", "techcrunch.", "cnet.", "wired.",
         "gizmodo.", "yankodesign.", "uncrate.", "trustpilot.", "shopify.dev", "help.shopify")


def queries(count: int) -> list[str]:
    combos = [t.format(c=c) for c in CATEGORIES for t in TEMPLATES]
    return websearch.rotate(combos, count)


def fetch(cfg: Config) -> Iterator[Lead]:
    if not websearch.available():
        return
    seen: set[str] = set()
    for q in queries(int(cfg.source("preorder_stores").get("queries_per_run", 12))):
        for r in websearch.search(q):
            url = r.get("url") or ""
            dom = domain_of(url)
            if not dom or is_platform(dom) or dom in seen or any(s in dom for s in _SKIP):
                continue
            seen.add(dom)
            name = (r.get("title") or dom).split("|")[0].split(" – ")[0].split(" - ")[0].strip()
            yield Lead(source="preorder_stores", source_url=url, name=name or dom, stage="preorder",
                       website=f"https://{dom}", description=r.get("snippet"),
                       extra={"product_url": url, "query": q})
