"""Agents 3+4 - Page audit and hyper-personalized outreach ("audit-first").

Claude reads what enrichment collected from the prospect's page, writes a
3-point conversion audit, and turns it into a short email sequence plus a
LinkedIn note (and, for forum leads, a helpful public reply + DM draft).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from ..config import Config
from ..llm import LLM
from ..models import Lead
from .enrich import summary_for_llm
from .qualify import Qualification


class Outreach(BaseModel):
    audit_points: list[str]          # 3 concrete pre-launch / conversion gaps
    subject: str
    email_body: str                  # plain text, no signature
    followup_1: str                  # sent in the same thread
    followup_2: str
    linkedin_note: str               # <= 280 characters
    forum_reply: Optional[str]       # helpful public reply, no pitch (forum leads only)
    forum_dm: Optional[str]          # private follow-up message (forum leads only)


ANGLE_GUIDE = {
    "preorder_pivot": "They sell pre-orders on their own store. Static pre-order pages convert ~1-2% with "
                      "no urgency; a 30-day Kickstarter/Indiegogo launch adds early-bird scarcity, platform "
                      "organic traffic, press interest and upfront cash for the factory MOQ, then feeds "
                      "backers back into their store. Frame crowdfunding as a launch event for the next batch.",
    "prelaunch_audit": "They have a coming-soon / pre-launch page. They are ~60-90 days from launch and "
                       "usually short of the 2,000-5,000 qualified emails a strong day one needs. Offer the "
                       "audit: $1 VIP reservation funnel, lead-capture hooks, reward-tier structure.",
    "patent_to_launch": "They were just granted a design patent, so tooling and a first production run are "
                        "close. Ask whether they plan to fund the first run through Kickstarter/Indiegogo.",
    "repeat_creator": "They crowdfunded before. Congratulate them, and pitch structuring their next-gen launch "
                      "so past backers and a VIP list give a guaranteed day one.",
    "forum_help": "Found on a forum/community. The public reply must be genuinely helpful and contain NO pitch "
                  "or link. The DM may mention the free page breakdown. Still write the email fields in case "
                  "an address is found.",
    "partnership": "An accelerator / hardware hub. Offer a free, equity-free 45-minute 'Crowdfunding "
                   "Go-To-Market Masterclass' for their current cohort plus a free crowdfunding readiness "
                   "audit for each startup. Address the program director/managing director.",
}

SYSTEM = """You write cold outreach for {agency} ({website}), a full-service Kickstarter/Indiegogo agency.
Style: audit-first. Lead with value specific to THEIR product, never a generic pitch.

Hard rules:
- Use only facts present in the prospect data. Never invent their numbers, features or names.
- Never invent results for {agency}. The only proof points you may use are: {proof}. If that list \
is empty, make no claims about past results or clients at all.
- First email: 70-130 words, plain text, no links, no images, no emojis, one soft question as the CTA \
(e.g. offer a 2-minute screen-recorded breakdown). Subject: under 8 words, lowercase-ish, no clickbait.
- Address the person by first name if a decision-maker name is known, otherwise "Hi {{product}} team".
- Do not include a signature, sign-off name or unsubscribe text; those are appended automatically.
- Follow-up 1 (day 3): 2-3 sentences adding one new insight from the audit. Follow-up 2 (day 7): \
a polite 1-2 sentence close-the-loop.
- Industry rules of thumb you may cite as general knowledge (not as {agency} results): pre-order pages \
usually convert ~1-2%; Kickstarter platform traffic often adds meaningful organic backers; a $1 \
reservation list warms buyers before launch.
"""


def _fallback(lead: Lead, q: Qualification, cfg: Config) -> Outreach:
    first = (q.decision_maker_name or "").split(" ")[0]
    hi = f"Hi {first}," if first else f"Hi {q.product_name} team,"
    product = q.product_name or lead.name
    body = {
        "preorder_pivot": f"{hi}\n\nI came across {product} and noticed you're taking pre-orders directly on "
                          "your site. Store pre-order pages usually convert around 1-2% because there's no "
                          "deadline or community momentum. A 30-day Kickstarter launch for the next batch "
                          "adds early-bird urgency, platform traffic and upfront cash for production.\n\n"
                          f"Would a 2-minute breakdown of how we'd structure {product} as a launch be useful?",
        "partnership": f"{hi}\n\nWe run Kickstarter and Indiegogo launches for hardware founders. We'd like to "
                       "offer your current cohort a free, equity-free 45-minute masterclass on raising a "
                       "first production run through crowdfunding, plus a free readiness audit per startup.\n\n"
                       "Would that be useful for your program this season?",
    }.get(q.pitch_angle, f"{hi}\n\nI came across {product}'s pre-launch page. Two things usually decide day "
                         "one: a $1 VIP reservation list and lead-capture hooks on the page. I noted a few "
                         f"quick wins for {product}.\n\nWould a 2-minute screen-recorded breakdown help?")
    return Outreach(audit_points=[], subject=f"quick idea for {product}"[:60], email_body=body,
                    followup_1=f"{hi}\n\nQuick follow-up: happy to send the breakdown for {product}, no strings.",
                    followup_2=f"{hi}\n\nI'll close the loop here. If launching {product} on Kickstarter is "
                               "on your roadmap later, just reply and I'll send it over.",
                    linkedin_note=f"Hi {first or 'there'}, came across {product}: I run crowdfunding launches "
                                  "for hardware brands and had a couple of ideas for it. Happy to connect.",
                    forum_reply=None, forum_dm=None)


def write(lead: Lead, q: Qualification, cfg: Config, llm: LLM) -> Outreach:
    if llm.enabled:
        proof = cfg.get_path("agency.proof_points") or []
        system = SYSTEM.format(agency=cfg.get_path("agency.name", "Cory Group"),
                               website=cfg.get_path("agency.website", ""),
                               proof="; ".join(proof) if proof else "(none)")
        user = (f"Pitch angle: {q.pitch_angle}\nAngle guidance: {ANGLE_GUIDE.get(q.pitch_angle, '')}\n\n"
                f"Qualification notes:\n{q.model_dump_json(indent=1)}\n\nProspect data:\n{summary_for_llm(lead)}\n\n"
                "Write the audit and the outreach.")
        out = llm.structured(model=cfg.get_path("llm.writer_model"), effort=cfg.get_path("llm.writer_effort"),
                             system=system, user=user, schema=Outreach, max_tokens=16000)
        if out:
            return out
    return _fallback(lead, q, cfg)
