"""Agent 2b - Qualification: scores each lead against Cory Group's ICP.

A free heuristic score runs first and filters out obvious misses, so the
paid Claude call is only spent on plausible prospects.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from ..config import Config
from ..llm import LLM
from ..models import Lead
from .enrich import summary_for_llm

PitchAngle = Literal["preorder_pivot", "prelaunch_audit", "patent_to_launch", "repeat_creator",
                     "forum_help", "partnership", "not_a_fit"]


class Qualification(BaseModel):
    fit_score: int                       # 0-100
    is_physical_product: bool
    product_name: str
    category: str
    stage: Literal["prelaunch", "preorder", "repeat_creator", "patent", "idea", "live_campaign",
                   "launched_retail", "accelerator", "unknown"]
    pitch_angle: PitchAngle
    decision_maker_name: Optional[str]
    decision_maker_role: Optional[str]
    estimated_price_usd: Optional[str]
    personalization_hooks: list[str]     # concrete facts from their page to reference
    reasons: list[str]
    red_flags: list[str]


SYSTEM = """You qualify sales prospects for Cory Group (corygroup.co), a full-service crowdfunding \
agency that runs Kickstarter and Indiegogo campaigns end to end: pre-launch list building ($1 VIP \
reservation funnels), Meta/TikTok ads, campaign page and video, PR outreach, and post-campaign \
transition to e-commerce.

Ideal client profile:
- A real physical product (consumer tech, gadgets, EDC, desk setups, outdoor gear, tabletop games, \
3D printers/lasers/maker tools, robotics, wearables, on-device AI hardware, AR/creator gear, design goods, \
apparel/footwear) with a retail price of roughly $50 or more.
- A team with some budget: a working prototype, a pre-order store with sales, a design patent, \
accelerator backing, or a previous successful campaign (repeat creators launching Gen 2 are excellent).
- Timing: pre-launch page live, taking website pre-orders, recently patented, or preparing a launch. \
A campaign that is ALREADY LIVE right now is a weaker fit (budget committed), score it lower.

Not a fit: software/apps/SaaS, films, music, books, charities, personal causes, services, \
dropshipped commodity goods, giant corporations, hobbyists with no product, and anything illegal.

Pitch angles: preorder_pivot (store taking pre-orders -> crowdfunding launch), prelaunch_audit \
(Kickstarter/Indiegogo coming-soon page), patent_to_launch (recent design patent), repeat_creator \
(past campaign, new product), forum_help (forum/community post; help first, never pitch publicly), \
partnership (accelerator/incubator/hub), not_a_fit.

Scoring: 80-100 strong ICP and timing; 60-79 good; 40-59 uncertain; below 40 not a fit. Only use \
facts present in the data; personalization_hooks must be specific, verifiable details from it."""


def heuristic_score(lead: Lead, cfg: Config) -> int:
    s = 30
    sig = " ".join(lead.signals)
    if "preorder_app:" in sig:
        s += 25
    if "shopify_preorder_products" in sig or "preorder_copy:" in sig:
        s += 15
    if "kickstarter_prelaunch_page" in sig or "indiegogo_page" in sig:
        s += 25
    if "previous_crowdfunding_success" in sig or "links_to_kickstarter" in sig:
        s += 10
    if "design_patent_granted" in sig:
        s += 15
    if lead.stage == "partner":
        s += 30
    blob = f"{lead.name} {lead.category or ''} {lead.description or ''} {lead.extra.get('page_text', '')[:3000]}".lower()
    if any(k in blob for k in cfg.get_path("signals.icp_categories", [])):
        s += 10
    if any(k in blob for k in cfg.get_path("signals.crowdfunding_intent", [])):
        s += 25 if lead.stage == "forum" else 10  # a creator saying they're about to launch
    if any(k in blob for k in ("app store", "saas", "subscription software", "album", "film", "novel", "charity")):
        s -= 25
    if lead.stage == "preorder" and "preorder_app:" not in sig and "preorder_copy:" not in sig:
        s -= 30  # search hit but no pre-order evidence on the live site
    if lead.extra.get("site_error"):
        s -= 20
    return max(0, min(100, s))


def qualify(lead: Lead, cfg: Config, llm: LLM) -> Qualification | None:
    """Returns None when the heuristic already rules the lead out."""
    h = heuristic_score(lead, cfg)
    lead.extra["heuristic_score"] = h
    if h < 35:
        return None
    if llm.enabled:
        q = llm.structured(model=cfg.get_path("llm.qualify_model"), effort=cfg.get_path("llm.qualify_effort"),
                           system=SYSTEM, user="Qualify this prospect:\n" + summary_for_llm(lead),
                           schema=Qualification, max_tokens=8000)
        if q:
            return q
    # Heuristic-only fallback (no API key or API failure).
    angle = {"preorder": "preorder_pivot", "prelaunch": "prelaunch_audit", "patent": "patent_to_launch",
             "forum": "forum_help", "partner": "partnership"}.get(lead.stage, "prelaunch_audit")
    if "previous_crowdfunding_success" in lead.signals:
        angle = "repeat_creator"
    return Qualification(fit_score=h, is_physical_product=True, product_name=lead.name,
                         category=lead.category or "unknown", stage="unknown", pitch_angle=angle,
                         decision_maker_name=lead.contact_name, decision_maker_role=lead.contact_role,
                         estimated_price_usd=lead.price, personalization_hooks=[], reasons=["heuristic"],
                         red_flags=[])
