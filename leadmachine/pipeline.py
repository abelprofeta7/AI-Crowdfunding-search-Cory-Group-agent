"""Orchestrator: one daily run of the whole lead machine.

  replies -> follow-ups -> [discover -> enrich -> qualify -> audit+write] x N -> send -> digest
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from . import report, sources
from .agents import enrich, qualify, replies, scheduler, writer
from .config import Config
from .db import DB
from .llm import LLM
from .models import Lead

log = logging.getLogger(__name__)


@dataclass
class RunStats:
    candidates: int = 0
    duplicates: int = 0
    disqualified: int = 0
    no_contact: int = 0
    ready_email: int = 0
    ready_manual: int = 0
    by_source: dict = field(default_factory=dict)
    send: dict = field(default_factory=dict)
    replies: dict = field(default_factory=dict)
    llm_cost_usd: float = 0.0
    seconds: int = 0

    @property
    def prospects(self) -> int:
        return self.ready_email + self.ready_manual


def process(lead: Lead, cfg: Config, db: DB, llm: LLM, stats: RunStats) -> str:
    """Runs one raw lead through enrich -> qualify -> write. Returns its final status."""
    raw_key = lead.key
    lead = enrich.enrich(lead, cfg)
    if lead.key != raw_key and db.seen(lead.key):
        db.mark_seen(raw_key)  # website discovered during enrichment is already known
        stats.duplicates += 1
        return "duplicate"
    if lead.extra.get("site_error") == "unreachable":
        return "unreachable"  # network hiccup: not stored, so it is retried on a later run
    if lead.id is None:
        if db.insert(lead) is None:
            stats.duplicates += 1
            return "duplicate"
    if db.recently_contacted_domain(lead.domain, int(cfg.get_path("pipeline.recontact_after_days", 180))):
        db.save(lead, status="duplicate_domain")
        stats.duplicates += 1
        return "duplicate_domain"

    q = qualify.qualify(lead, cfg, llm)
    min_fit = int(cfg.get_path("pipeline.min_fit_score", 60))
    product_ok = q is not None and (q.is_physical_product or q.pitch_angle == "partnership")
    if q is None or q.fit_score < min_fit or q.pitch_angle == "not_a_fit" or not product_ok:
        db.save(lead, status="disqualified", fit_score=q.fit_score if q else lead.extra.get("heuristic_score"),
                qualification=q.model_dump() if q else None)
        stats.disqualified += 1
        return "disqualified"
    lead.contact_name = lead.contact_name or q.decision_maker_name
    lead.contact_role = lead.contact_role or q.decision_maker_role

    manual = lead.stage == "forum" or q.pitch_angle == "forum_help"
    if not lead.email and not manual:
        db.save(lead, status="no_email", fit_score=q.fit_score, qualification=q.model_dump())
        stats.no_contact += 1
        return "no_email"

    out = writer.write(lead, q, cfg, llm)
    status = "drafted" if lead.email else "manual"
    db.save(lead, status=status, fit_score=q.fit_score, qualification=q.model_dump(), outreach=out.model_dump())
    if status == "drafted":
        stats.ready_email += 1
    else:
        stats.ready_manual += 1
    stats.by_source[lead.source] = stats.by_source.get(lead.source, 0) + 1
    return status


def discover(cfg: Config, db: DB, llm: LLM, stats: RunStats, only: list[str] | None = None) -> None:
    target = int(cfg.get_path("pipeline.daily_target", 50))
    max_candidates = int(cfg.get_path("pipeline.max_candidates_per_run", 400))
    for lead in sources.interleave(cfg, only):
        if stats.prospects >= target or stats.candidates >= max_candidates:
            break
        if not lead.name or db.seen(lead.key):
            stats.duplicates += 1
            continue
        stats.candidates += 1
        try:
            status = process(lead, cfg, db, llm, stats)
            log.info("[%s] %s -> %s", lead.source, lead.name[:60], status)
        except Exception:  # noqa: BLE001 - one bad lead must not stop the run
            log.exception("failed processing %s", lead.source_url)


def run_daily(cfg: Config, db: DB, llm: LLM | None = None, *, send: bool = True,
              only: list[str] | None = None, notify: bool = True) -> RunStats:
    t0 = time.time()
    llm = llm or LLM()
    stats = RunStats()
    if send:
        stats.replies = replies.run(cfg, db, llm)
    discover(cfg, db, llm, stats, only)
    if send:
        stats.send = scheduler.run(cfg, db)
    stats.llm_cost_usd = llm.cost_usd()
    stats.seconds = int(time.time() - t0)
    report.publish(cfg, db, stats, send_notifications=notify)
    return stats
