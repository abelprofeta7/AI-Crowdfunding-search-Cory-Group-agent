"""CLI.

  python -m leadmachine run                 # full daily run (replies, discovery, sending, digest)
  python -m leadmachine replies             # inbox + follow-ups only (cheap, run every few hours)
  python -m leadmachine discover --no-send --source preorder_stores --target 5
  python -m leadmachine export leads.csv    # every lead to CSV
  python -m leadmachine stats
  python -m leadmachine suppress someone@example.com
  python -m leadmachine decrypt | encrypt   # data/leads.db <-> data/leads.db.enc
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import ROOT, load_config


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="leadmachine")
    p.add_argument("--config")
    p.add_argument("--db")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("replies")
    d = sub.add_parser("discover")
    d.add_argument("--source", action="append", help="limit to these sources (repeatable)")
    d.add_argument("--target", type=int)
    d.add_argument("--no-send", action="store_true")
    e = sub.add_parser("export")
    e.add_argument("path", nargs="?", default="out/leads.csv")
    sub.add_parser("stats")
    s = sub.add_parser("suppress")
    s.add_argument("value", help="email or @domain")
    sub.add_parser("encrypt")
    sub.add_parser("decrypt")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("urllib3", "httpx", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    plain, enc = ROOT / "data" / "leads.db", ROOT / "data" / "leads.db.enc"
    if a.cmd in ("encrypt", "decrypt"):
        from . import vault
        if a.cmd == "encrypt":
            vault.encrypt(plain, enc)
        elif enc.exists():
            vault.decrypt(enc, plain)
        return 0

    from .db import DB
    cfg = load_config(a.config)
    db = DB(a.db)
    try:
        if a.cmd == "run":
            from .pipeline import run_daily
            run_daily(cfg, db)
        elif a.cmd == "replies":
            from .agents import replies, scheduler
            from .llm import LLM
            r = replies.run(cfg, db, LLM())
            if r.get("hot"):
                from . import notify
                notify.webhook("Hot replies:\n" + "\n".join(f"- {h['name']}: {h['intent']} - {h['summary']}" for h in r["hot"]))
            scheduler.run(cfg, db)
        elif a.cmd == "discover":
            from .pipeline import run_daily
            if a.target:
                cfg["pipeline"]["daily_target"] = a.target
            run_daily(cfg, db, send=not a.no_send, only=a.source)
        elif a.cmd == "export":
            from .report import lead_rows, to_csv
            Path(a.path).parent.mkdir(parents=True, exist_ok=True)
            Path(a.path).write_text(to_csv(lead_rows(db)), encoding="utf-8")
            print(f"wrote {a.path}")
        elif a.cmd == "stats":
            for status, n in db.conn.execute("SELECT status, COUNT(*) FROM leads GROUP BY status ORDER BY 2 DESC"):
                print(f"{status:18} {n}")
            print(f"{'sent today':18} {db.sent_today()}")
        elif a.cmd == "suppress":
            db.suppress(a.value, "manual")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
