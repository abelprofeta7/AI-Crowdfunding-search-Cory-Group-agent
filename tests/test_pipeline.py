import json
from datetime import date, datetime, timedelta, timezone

import pytest

from leadmachine import pipeline, sources
from leadmachine.agents import enrich, scheduler
from leadmachine.config import load_config
from leadmachine.db import DB
from leadmachine.llm import LLM
from leadmachine.models import Lead


@pytest.fixture
def cfg():
    c = load_config()
    c["outreach"]["warmup_start_date"] = None
    return c


@pytest.fixture
def db(tmp_path):
    d = DB(tmp_path / "t.db")
    yield d
    d.close()


def fake_enrich(lead, cfg):
    if lead.stage == "preorder":
        lead.email, lead.email_source = f"founder@{lead.domain}", "scraped"
        lead.signals += ["preorder_app:purple_dot", "shopify_preorder_products"]
        lead.extra["page_text"] = "Pre-order our EDC multi-tool gadget. Ships in May."
    return lead


def test_full_run_offline(cfg, db, monkeypatch, tmp_path):
    leads = [Lead(source="preorder_stores", source_url=f"https://brand{i}.example/p", name=f"Brand {i}",
                  stage="preorder", website=f"https://brand{i}.example") for i in range(4)]
    leads.append(Lead(source="reddit", source_url="https://reddit.com/r/kickstarter/1", name="Launching our board game on Kickstarter",
                      stage="forum", description="prelaunch page live, tabletop game"))
    leads.append(Lead(source="preorder_stores", source_url="https://dup.example", name="Dup", stage="preorder",
                      website="https://brand0.example"))  # same domain as Brand 0
    monkeypatch.setattr(sources, "interleave", lambda cfg, only=None: iter(leads))
    monkeypatch.setattr(enrich, "enrich", fake_enrich)
    monkeypatch.setattr("leadmachine.report.OUT", tmp_path)
    llm = LLM()
    llm.enabled = False

    stats = pipeline.run_daily(cfg, db, llm, send=False, notify=False)
    assert stats.ready_email == 4
    assert stats.ready_manual == 1
    assert stats.duplicates == 1
    row = db.rows("status='drafted'")[0]
    out = json.loads(row["outreach"])
    assert out["subject"] and "pre-order" in out["email_body"]
    assert (tmp_path / f"digest-{date.today().isoformat()}.md").exists()

    # dry-run sending respects the warm-up cap (week 0 -> 10), all 4 go out
    s = scheduler.run(cfg, db, dry_run=True, jitter=False)
    assert s["sent_initial"] == 4 and s["cap"] == 10
    assert len(db.rows("status='contacted'")) == 4

    # follow-up 1 is due 3 days later, in the same thread
    old = (datetime.now(timezone.utc) - timedelta(days=3, minutes=1)).isoformat()
    db.conn.execute("UPDATE leads SET last_contacted_at=?", (old,))
    s = scheduler.run(cfg, db, dry_run=True, jitter=False)
    assert s["sent_followups"] == 4 and s["sent_initial"] == 0
    msgs = db.conn.execute("SELECT subject FROM messages WHERE kind='followup'").fetchall()
    assert all(m[0].startswith("Re: ") for m in msgs)

    # suppressed addresses are never emailed
    db.suppress("@brand1.example", "test")
    assert db.is_suppressed("founder@brand1.example")


def test_daily_target_stops_discovery(cfg, db, monkeypatch, tmp_path):
    cfg["pipeline"]["daily_target"] = 2
    leads = (Lead(source="preorder_stores", source_url=f"https://b{i}.example", name=f"B{i}", stage="preorder",
                  website=f"https://b{i}.example") for i in range(50))
    monkeypatch.setattr(sources, "interleave", lambda cfg, only=None: leads)
    monkeypatch.setattr(enrich, "enrich", fake_enrich)
    monkeypatch.setattr("leadmachine.report.OUT", tmp_path)
    llm = LLM(); llm.enabled = False
    stats = pipeline.run_daily(cfg, db, llm, send=False, notify=False)
    assert stats.prospects == 2 and stats.candidates == 2


def test_no_preorder_evidence_is_disqualified(cfg, db, monkeypatch, tmp_path):
    lead = Lead(source="preorder_stores", source_url="https://shoes.example", name="Shoes", stage="preorder",
                website="https://shoes.example")
    monkeypatch.setattr(sources, "interleave", lambda cfg, only=None: iter([lead]))
    monkeypatch.setattr(enrich, "enrich", lambda l, c: l)
    monkeypatch.setattr("leadmachine.report.OUT", tmp_path)
    llm = LLM(); llm.enabled = False
    stats = pipeline.run_daily(cfg, db, llm, send=False, notify=False)
    assert stats.disqualified == 1 and stats.prospects == 0


def test_warmup_ramp(cfg, db):
    cfg["outreach"]["warmup_start_date"] = (date.today() - timedelta(days=15)).isoformat()
    assert scheduler.daily_cap(cfg, db) == 20
    cfg["outreach"]["warmup_start_date"] = (date.today() - timedelta(days=200)).isoformat()
    assert scheduler.daily_cap(cfg, db) == 50
