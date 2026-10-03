"""Agent 5a - Scheduler: sends first emails and in-thread follow-ups with a
warm-up ramp, a daily cap, a sending window and the suppression list."""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import date, datetime, timezone

from .. import mailer
from ..config import Config
from ..db import DB, now

log = logging.getLogger(__name__)


def daily_cap(cfg: Config, db: DB) -> int:
    target = int(cfg.get_path("pipeline.daily_target", 50))
    schedule = cfg.get_path("outreach.warmup_schedule") or [target]
    start = cfg.get_path("outreach.warmup_start_date") or db.get_meta("warmup_start")
    if not start:
        start = date.today().isoformat()
        db.set_meta("warmup_start", start)
    weeks = max(0, (date.today() - date.fromisoformat(str(start))).days // 7)
    return min(target, int(schedule[min(weeks, len(schedule) - 1)]))


def in_window(cfg: Config) -> bool:
    lo, hi = cfg.get_path("outreach.send_window_utc") or [0, 24]
    return lo <= datetime.now(timezone.utc).hour < hi


def _days_since(ts: str | None) -> float:
    if not ts:
        return 1e9
    return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 86400


def _jitter() -> None:
    time.sleep(random.uniform(5, 15))  # spread sends out; looks human, protects reputation


def run(cfg: Config, db: DB, *, dry_run: bool = False, jitter: bool = True) -> dict:
    stats = {"sent_initial": 0, "sent_followups": 0, "skipped_reason": None, "cap": 0}
    if not cfg.get_path("outreach.auto_send", False):
        stats["skipped_reason"] = "auto_send is off (drafts only)"
        return stats
    if mailer.placeholders_left(cfg) and not dry_run:
        stats["skipped_reason"] = "fill in agency.sender_name and agency.postal_address in config.yaml"
        return stats
    if not mailer.configured() and not dry_run:
        stats["skipped_reason"] = "SMTP not configured (drafts only)"
        return stats
    if not in_window(cfg) and not dry_run:
        stats["skipped_reason"] = "outside send window"
        return stats

    cap = daily_cap(cfg, db)
    stats["cap"] = cap
    followup_days = cfg.get_path("outreach.followup_days") or []

    def send(sender, row, subject, body, kind, reply_to=None):
        msg = mailer.build(cfg, row["email"], subject, body, in_reply_to=reply_to)
        mid = msg["Message-ID"] if dry_run else sender.send(msg)
        db.log_message(row["id"], "out", kind, subject, body, mid)
        return mid

    class _Null:
        def __enter__(self): return self
        def __exit__(self, *a): pass

    with (_Null() if dry_run else mailer.Sender()) as sender:
        # 1) follow-ups (never count against the cap of new prospects)
        for row in db.rows("status='contacted' AND email IS NOT NULL"):
            step = row["step"]
            if step - 1 >= len(followup_days):
                db.update(row["id"], status="finished")
                continue
            if _days_since(row["last_contacted_at"]) < followup_days[step - 1] or db.is_suppressed(row["email"]):
                continue
            out = json.loads(row["outreach"] or "{}")
            body = out.get(f"followup_{step}")
            if not body:
                continue
            subj = out.get("subject", "")
            try:
                send(sender, row, subj if subj.lower().startswith("re:") else f"Re: {subj}", body,
                     "followup", row["thread_message_id"])
            except Exception as exc:  # noqa: BLE001
                log.error("follow-up to %s failed: %s", row["email"], exc)
                continue
            db.update(row["id"], step=step + 1, last_contacted_at=now())
            stats["sent_followups"] += 1
            if jitter and not dry_run:
                _jitter()

        # 2) first emails, best-fit first
        remaining = cap - db.sent_today()
        for row in db.rows("status='drafted' AND email IS NOT NULL", order="fit_score DESC, id"):
            if remaining <= 0:
                break
            if db.is_suppressed(row["email"]):
                db.update(row["id"], status="suppressed")
                continue
            if db.recently_contacted_domain(row["domain"], int(cfg.get_path("pipeline.recontact_after_days", 180))):
                db.update(row["id"], status="duplicate_domain")
                continue
            out = json.loads(row["outreach"] or "{}")
            try:
                mid = send(sender, row, out["subject"], out["email_body"], "initial")
            except Exception as exc:  # noqa: BLE001
                log.error("send to %s failed: %s", row["email"], exc)
                continue
            db.update(row["id"], status="contacted", step=1, thread_message_id=mid, last_contacted_at=now())
            stats["sent_initial"] += 1
            remaining -= 1
            if jitter and not dry_run:
                _jitter()
    return stats
