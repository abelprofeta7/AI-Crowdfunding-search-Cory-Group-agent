"""SQLite store for leads, sent messages and the suppression list."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import ROOT
from .models import Lead

DEFAULT_PATH = ROOT / "data" / "leads.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    domain TEXT,
    source TEXT NOT NULL,
    source_url TEXT,
    name TEXT,
    stage TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    email TEXT,
    fit_score INTEGER,
    data TEXT NOT NULL,              -- full Lead as JSON
    qualification TEXT,              -- qualification agent JSON
    outreach TEXT,                   -- writer agent JSON (subject/body/follow-ups)
    step INTEGER NOT NULL DEFAULT 0, -- emails sent so far
    thread_message_id TEXT,
    last_contacted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER REFERENCES leads(id),
    direction TEXT NOT NULL,         -- out | in
    kind TEXT,                       -- initial | followup | reply | auto_reply
    subject TEXT,
    body TEXT,
    message_id TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppression (
    value TEXT PRIMARY KEY,          -- email address or @domain
    reason TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY);   -- source URLs that resolved to known leads
"""

# Lead lifecycle:
# new -> enriched -> qualified -> drafted -> contacted -> replied/interested/booked
# side exits: disqualified, no_email, unsubscribed, bounced, not_interested, finished
ACTIVE_STATUSES = ("contacted",)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # ---- leads -------------------------------------------------------------
    def seen(self, key: str) -> bool:
        return (self.conn.execute("SELECT 1 FROM leads WHERE key=?", (key,)).fetchone() is not None
                or self.conn.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone() is not None)

    def mark_seen(self, key: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO seen(key) VALUES(?)", (key,))
        self.conn.commit()

    def recently_contacted_domain(self, domain: str | None, days: int) -> bool:
        if not domain:
            return False
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        row = self.conn.execute(
            "SELECT 1 FROM leads WHERE domain=? AND last_contacted_at IS NOT NULL AND last_contacted_at>?",
            (domain, cutoff)).fetchone()
        return row is not None

    def insert(self, lead: Lead, status: str = "new") -> int | None:
        ts = now()
        try:
            cur = self.conn.execute(
                "INSERT INTO leads(key,domain,source,source_url,name,stage,status,email,data,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (lead.key, lead.domain, lead.source, lead.source_url, lead.name, lead.stage, status,
                 lead.email, json.dumps(lead.to_dict()), ts, ts))
        except sqlite3.IntegrityError:
            return None
        self.conn.commit()
        lead.id = cur.lastrowid
        return lead.id

    def save(self, lead: Lead, **fields: Any) -> None:
        # Keep the database small: full page text only matters for leads we'll contact.
        keep = fields.get("status") in ("drafted", "manual")
        for k in ("page_text", "products"):
            if k in lead.extra and not keep:
                lead.extra.pop(k)
        if keep and lead.extra.get("page_text"):
            lead.extra["page_text"] = lead.extra["page_text"][:2500]
        cols = {"data": json.dumps(lead.to_dict()), "email": lead.email, "domain": lead.domain,
                "stage": lead.stage, "name": lead.name, "updated_at": now()}
        for k, v in fields.items():
            cols[k] = json.dumps(v) if isinstance(v, (dict, list)) else v
        sets = ",".join(f"{k}=?" for k in cols)
        self.conn.execute(f"UPDATE leads SET {sets} WHERE id=?", (*cols.values(), lead.id))
        self.conn.commit()

    def update(self, lead_id: int, **fields: Any) -> None:
        fields["updated_at"] = now()
        for k, v in list(fields.items()):
            if isinstance(v, (dict, list)):
                fields[k] = json.dumps(v)
        sets = ",".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE leads SET {sets} WHERE id=?", (*fields.values(), lead_id))
        self.conn.commit()

    def rows(self, where: str = "1=1", params: Iterable[Any] = (), order: str = "id") -> list[sqlite3.Row]:
        return self.conn.execute(f"SELECT * FROM leads WHERE {where} ORDER BY {order}", tuple(params)).fetchall()

    @staticmethod
    def lead_from_row(row: sqlite3.Row) -> Lead:
        data = json.loads(row["data"])
        data["id"] = row["id"]
        return Lead(**data)

    def find_by_email(self, email: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM leads WHERE lower(email)=lower(?) ORDER BY id DESC",
                                 (email,)).fetchone()

    def find_by_domain(self, domain: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM leads WHERE domain=? ORDER BY id DESC", (domain,)).fetchone()

    # ---- messages ----------------------------------------------------------
    def log_message(self, lead_id: int | None, direction: str, kind: str, subject: str,
                    body: str, message_id: str | None) -> None:
        self.conn.execute(
            "INSERT INTO messages(lead_id,direction,kind,subject,body,message_id,at) VALUES(?,?,?,?,?,?,?)",
            (lead_id, direction, kind, subject, body, message_id, now()))
        self.conn.commit()

    def sent_today(self) -> int:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        return self.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE direction='out' AND kind='initial' AND at>=?",
            (start,)).fetchone()[0]

    # ---- suppression -------------------------------------------------------
    def suppress(self, value: str, reason: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO suppression(value,reason,at) VALUES(?,?,?)",
                          (value.lower(), reason, now()))
        self.conn.commit()

    def is_suppressed(self, email: str | None) -> bool:
        if not email:
            return False
        email = email.lower()
        dom = "@" + email.split("@")[-1]
        return self.conn.execute("SELECT 1 FROM suppression WHERE value IN (?,?)",
                                 (email, dom)).fetchone() is not None

    # ---- meta --------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))
        self.conn.commit()
