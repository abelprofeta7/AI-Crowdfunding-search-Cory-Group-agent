"""Daily digest (Markdown + CSV) and exports. Lead details go to your inbox /
webhook and the local out/ folder only, never to the (public) CI logs."""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from . import notify
from .config import ROOT, Config
from .db import DB

log = logging.getLogger(__name__)
OUT = ROOT / "out"

CSV_FIELDS = ["id", "status", "fit_score", "source", "stage", "name", "company", "website", "contact_name",
              "contact_role", "email", "email_source", "linkedin", "category", "price", "pitch_angle",
              "subject", "email_body", "linkedin_note", "forum_reply", "forum_dm", "source_url", "created_at"]


def lead_rows(db: DB, where: str = "1=1", params=()) -> list[dict]:
    out = []
    for row in db.rows(where, params):
        lead = db.lead_from_row(row)
        q = json.loads(row["qualification"] or "{}")
        o = json.loads(row["outreach"] or "{}")
        out.append({
            "id": row["id"], "status": row["status"], "fit_score": row["fit_score"], "source": lead.source,
            "stage": lead.stage, "name": lead.name, "company": lead.company, "website": lead.website,
            "contact_name": lead.contact_name, "contact_role": lead.contact_role, "email": lead.email,
            "email_source": lead.email_source, "linkedin": lead.linkedin, "category": q.get("category") or lead.category,
            "price": q.get("estimated_price_usd") or lead.price, "pitch_angle": q.get("pitch_angle"),
            "subject": o.get("subject"), "email_body": o.get("email_body"), "linkedin_note": o.get("linkedin_note"),
            "forum_reply": o.get("forum_reply"), "forum_dm": o.get("forum_dm"), "source_url": lead.source_url,
            "created_at": row["created_at"], "hooks": q.get("personalization_hooks") or [],
            "audit": o.get("audit_points") or [],
        })
    return out


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def build_markdown(cfg: Config, stats, today_rows: list[dict], totals: dict) -> str:
    d = date.today().isoformat()
    s = stats
    email_rows = [r for r in today_rows if r["status"] in ("drafted", "contacted")]
    manual_rows = [r for r in today_rows if r["status"] == "manual"]
    research = [r for r in today_rows if r["status"] == "no_email" and (r["fit_score"] or 0) >= 70]
    send = s.send or {}
    rep = s.replies or {}
    lines = [
        f"# Cory Group lead machine - {d}",
        "",
        f"**{s.prospects} new prospects** today ({s.ready_email} email-ready, {s.ready_manual} community/manual) "
        f"from {s.candidates} candidates screened. Target: {cfg.get_path('pipeline.daily_target')}.",
        f"Emails sent: {send.get('sent_initial', 0)} first touches (cap {send.get('cap', '-')}), "
        f"{send.get('sent_followups', 0)} follow-ups." + (f" Note: {send['skipped_reason']}." if send.get("skipped_reason") else ""),
        f"Replies: {rep.get('replies', 0)} ({rep.get('interested', 0)} interested, {rep.get('unsubscribed', 0)} opted out, "
        f"{rep.get('bounced', 0)} bounced). AI cost today: ${s.llm_cost_usd:.2f}. Runtime {s.seconds}s.",
        f"By source: {', '.join(f'{k} {v}' for k, v in sorted(s.by_source.items())) or 'none'}.",
        f"Pipeline totals: {', '.join(f'{k} {v}' for k, v in sorted(totals.items()))}.",
        "",
    ]
    if rep.get("hot"):
        lines += ["## Hot replies - act now", ""]
        for h in rep["hot"]:
            lines.append(f"- **{h['name']}** <{h['email']}> - {h['intent']}: {h['summary']}"
                         + (" (auto-replied with booking link)" if h.get("auto_replied") else ""))
            if h.get("draft") and not h.get("auto_replied"):
                lines.append(f"  - Suggested reply: {h['draft']}")
        lines.append("")
    if email_rows:
        lines += ["## New email prospects", "", "| Fit | Prospect | Stage | Contact | Angle |", "|---|---|---|---|---|"]
        for r in sorted(email_rows, key=lambda r: -(r["fit_score"] or 0)):
            who = " ".join(x for x in (r["contact_name"], f"<{r['email']}>") if x)
            lines.append(f"| {r['fit_score']} | [{r['name'][:50]}]({r['website'] or r['source_url']}) | "
                         f"{r['stage']} | {who} | {r['pitch_angle']} |")
        lines.append("")
    if manual_rows:
        lines += ["## Community engagement queue (post manually - never pitch in public)", ""]
        for r in manual_rows:
            lines.append(f"### [{r['name'][:80]}]({r['source_url']}) - {r['contact_name'] or ''}")
            if r.get("forum_reply"):
                lines.append(f"**Helpful public reply:** {r['forum_reply']}")
            if r.get("forum_dm"):
                lines.append(f"\n**DM after replying:** {r['forum_dm']}")
            lines.append("")
    if research:
        lines += ["## High-fit, no email found (LinkedIn / contact form)", ""]
        for r in research:
            lines.append(f"- {r['fit_score']} - [{r['name'][:60]}]({r['website'] or r['source_url']})"
                         + (f" - LinkedIn: {r['linkedin']}" if r.get("linkedin") else "")
                         + (f"\n  - Note: {r['linkedin_note']}" if r.get("linkedin_note") else ""))
        lines.append("")
    lines.append("Full details (audits, email copy) are in the attached CSV.")
    return "\n".join(lines)


def publish(cfg: Config, db: DB, stats, *, send_notifications: bool = True) -> Path:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    today_rows = lead_rows(db, "created_at>=? AND status IN ('drafted','contacted','manual','no_email')", (start,))
    totals = dict(db.conn.execute("SELECT status, COUNT(*) FROM leads GROUP BY status").fetchall())
    md = build_markdown(cfg, stats, today_rows, totals)
    OUT.mkdir(exist_ok=True)
    d = date.today().isoformat()
    path = OUT / f"digest-{d}.md"
    path.write_text(md, encoding="utf-8")
    csv_text = to_csv(today_rows)
    (OUT / f"prospects-{d}.csv").write_text(csv_text, encoding="utf-8")
    log.info("digest: %s prospects (%s email, %s manual), %s candidates, cost $%.2f",
             stats.prospects, stats.ready_email, stats.ready_manual, stats.candidates, stats.llm_cost_usd)
    if send_notifications:
        notify.email_digest(cfg, f"Lead machine: {stats.prospects} new prospects ({d})", md, csv_text)
        notify.webhook(md)
    return path
