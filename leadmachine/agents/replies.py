"""Agent 5b - Conversational qualification: reads the inbox, matches replies to
leads, stops sequences, honours opt-outs, records bounces, and answers
interested prospects with the booking link."""
from __future__ import annotations

import json
import logging
import re
from typing import Literal, Optional

from pydantic import BaseModel

from .. import mailer
from ..config import Config
from ..db import DB
from ..llm import LLM

log = logging.getLogger(__name__)


class ReplyAnalysis(BaseModel):
    intent: Literal["interested", "question", "not_interested", "unsubscribe", "out_of_office",
                    "referral", "wrong_person", "other"]
    summary: str
    objection: Optional[str]
    suggested_reply: Optional[str]   # plain text, no signature


SYSTEM = """You handle replies to cold emails from {agency}, a full-service Kickstarter/Indiegogo agency.
Classify the prospect's reply and, when they are interested or asked a question, draft a short, warm \
answer (under 120 words) that answers what they asked and proposes a 15-minute call via {link}.
Common objections and how to answer them honestly:
- "Why pay Kickstarter 5% when I have Shopify?" Platform traffic and urgency usually bring organic \
backers on top of paid ads; backers flow back into their store afterwards.
- Fees: performance-based vs retainer structures exist; details depend on the project and are \
discussed on the call. Never quote a specific price or invent client results.
If they refer you to someone else, thank them and ask for an intro. Do not include a signature."""

BOUNCE_FROM = ("mailer-daemon", "postmaster", "mail delivery")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _keyword_intent(body: str) -> str:
    low = body.lower()
    if any(k in low for k in ("unsubscribe", "remove me", "stop emailing", "do not contact")):
        return "unsubscribe"
    if any(k in low for k in ("out of office", "on vacation", "away until", "auto-reply", "automatic reply")):
        return "out_of_office"
    if any(k in low for k in ("not interested", "no thanks", "no thank you", "not a fit", "pass on this")):
        return "not_interested"
    if any(k in low for k in ("send it", "sounds good", "interested", "let's chat", "lets chat", "call", "sure")):
        return "interested"
    return "other"


def _strip_quoted(body: str) -> str:
    out = []
    for line in body.splitlines():
        if line.startswith(">") or re.match(r"^On .+ wrote:$", line.strip()):
            break
        out.append(line)
    return "\n".join(out).strip()


def run(cfg: Config, db: DB, llm: LLM) -> dict:
    stats = {"replies": 0, "interested": 0, "unsubscribed": 0, "bounced": 0, "auto_replied": 0, "hot": []}
    if not mailer.configured():
        return stats
    seen = {r[0] for r in db.conn.execute("SELECT message_id FROM messages WHERE direction='in'")}
    our_ids = {r[0]: r[1] for r in db.conn.execute(
        "SELECT message_id, lead_id FROM messages WHERE direction='out' AND message_id IS NOT NULL")}
    me = mailer.from_email().lower()
    try:
        inbox = list(mailer.fetch_recent())
    except Exception as exc:  # noqa: BLE001
        log.error("IMAP read failed: %s", exc)
        return stats

    for m in inbox:
        if not m["message_id"] or m["message_id"] in seen or m["from"] == me:
            continue
        # Bounces: find which lead's address appears in the bounce body.
        if any(b in (m["from"] + " " + m["subject"]).lower() for b in BOUNCE_FROM):
            for addr in set(a.lower() for a in _EMAIL_RE.findall(m["body"])):
                row = db.find_by_email(addr)
                if row and row["status"] in ("contacted", "finished"):
                    db.update(row["id"], status="bounced")
                    db.suppress(addr, "bounce")
                    stats["bounced"] += 1
            db.log_message(None, "in", "bounce", m["subject"], m["body"][:2000], m["message_id"])
            continue
        lead_id = next((our_ids[i] for i in re.findall(r"<[^>]+>", m["in_reply_to"] + " " + m["references"])
                        if i in our_ids), None)
        row = db.conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone() if lead_id else None
        row = row or db.find_by_email(m["from"]) or db.find_by_domain(m["from"].split("@")[-1])
        if not row or not row["step"]:
            continue  # not a reply from someone we emailed
        stats["replies"] += 1
        body = _strip_quoted(m["body"])
        db.log_message(row["id"], "in", "reply", m["subject"], body, m["message_id"])

        analysis = None
        if llm.enabled:
            system = SYSTEM.format(agency=cfg.get_path("agency.name"), link=cfg.get_path("agency.booking_link"))
            ctx = json.loads(row["outreach"] or "{}")
            analysis = llm.structured(
                model=cfg.get_path("llm.replies_model"), effort=cfg.get_path("llm.replies_effort"),
                system=system, schema=ReplyAnalysis, max_tokens=8000,
                user=f"Our first email:\n{ctx.get('email_body', '')}\n\nTheir reply from {m['from_name']} <{m['from']}>:\n{body}")
        intent = analysis.intent if analysis else _keyword_intent(body)

        if intent in ("unsubscribe", "not_interested"):
            db.suppress(m["from"], intent)
            db.update(row["id"], status="unsubscribed" if intent == "unsubscribe" else "not_interested")
            stats["unsubscribed"] += 1
            continue
        if intent == "out_of_office":
            continue  # sequence keeps going
        new_status = "interested" if intent in ("interested", "question", "referral") else "replied"
        db.update(row["id"], status=new_status)
        hot = {"name": row["name"], "email": m["from"], "intent": intent,
               "summary": analysis.summary if analysis else body[:200],
               "draft": analysis.suggested_reply if analysis else None}
        if new_status == "interested":
            stats["interested"] += 1
            if cfg.get_path("outreach.auto_reply_interested") and hot["draft"]:
                try:
                    with mailer.Sender() as s:
                        subj = m["subject"] if m["subject"].lower().startswith("re:") else f"Re: {m['subject']}"
                        msg = mailer.build(cfg, m["from"], subj, hot["draft"], in_reply_to=m["message_id"],
                                           with_footer=False)
                        msg.set_content(hot["draft"].strip() + f"\n\n{cfg.get_path('agency.sender_name')}\n"
                                        f"{cfg.get_path('agency.name')}")
                        db.log_message(row["id"], "out", "auto_reply", subj, hot["draft"], s.send(msg))
                    hot["auto_replied"] = True
                    stats["auto_replied"] += 1
                except Exception as exc:  # noqa: BLE001
                    log.error("auto-reply failed: %s", exc)
        stats["hot"].append(hot)
    return stats
