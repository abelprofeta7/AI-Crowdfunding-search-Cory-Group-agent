# Cory Group lead machine

An automated, AI-agent pipeline that finds people about to launch a physical product
on Kickstarter or Indiegogo (or who should), qualifies them against Cory Group's
ideal client profile, writes an audit-first personalized pitch, sends it with
follow-ups, and handles the replies. It implements the "Crowdfunding Agency Client
Acquisition Strategy" document, on a near-zero budget:

- It runs free on **GitHub Actions** (no server).
- **Claude** handles the thinking (qualification, page audits, copy, reply handling).
- Everything else is free APIs and plain web scraping.

```
            ┌──────────────── daily, Mon-Fri ─────────────────┐
 Sources ──▶ Agent 1   ──▶ Agent 2a  ──▶ Agent 2b    ──▶ Agent 3+4         ──▶ Agent 5a   ──▶ Digest
            Scraping      Enrichment     Qualification   Audit + outreach      Scheduler      (email / Slack /
            (8 channels)  site crawl,    ICP score 0-100 3-point audit, email  warm-up cap,   Discord + CSV)
                          emails, signals (Claude)       sequence, LinkedIn,   follow-ups
                                                         forum reply (Claude)
            Agent 5b, every 3 hours: inbox, then classify replies, opt-outs, bounces,
            and auto-answer interested prospects with your booking link (Claude)
```

## Channels (from the strategy doc)

| Source | What it finds | Cost / key |
|---|---|---|
| `preorder_stores` | Shopify/Woo stores selling unreleased products on pre-order. Pre-order app fingerprints (Purple Dot, PreProduct, Kaching, Globo, Downpay, ...) are confirmed on the live site | web search key (free tier) |
| `kickstarter_upcoming` | "Coming soon" pre-launch pages (60-90 days before launch) | free (web-search fallback if Kickstarter blocks) |
| `indiegogo` | Indiegogo coming-soon pages | web search key |
| `patents` | Design patents granted to small US companies in the last 90 days | free PatentsView key |
| `producthunt` | New hardware launches | free developer token |
| `hackernews` | Show HN / crowdfunding posts | free, no key |
| `reddit` | Creators preparing launches in r/kickstarter, r/crowdfunding, r/hardware, ... | free script app (recommended) |
| `accelerators` | HAX, mHUB, Greentown, Brinc, ... partnership offer (masterclass) | free (`data/accelerators.yaml`) |

Pitch angles are chosen per lead: `preorder_pivot`, `prelaunch_audit`, `patent_to_launch`,
`repeat_creator` (past campaign, next-gen product), `forum_help`, and `partnership`.

Reddit and Hacker News leads are **never auto-messaged**, because that breaks those
sites' rules and gets accounts banned. They go into the digest's "community
engagement queue" with a drafted helpful reply and a DM for you to post by hand.
LinkedIn is the same: you get a drafted connection note, but nothing is automated
there, because automation gets LinkedIn accounts restricted.

## Is 50 prospects a day realistic?

Yes, with two caveats:

1. **Discovery reaches 50 a day right away.** With a search key configured, one daily
   run screens up to 400 candidates. Rough yield per run:
   - 25-35 pre-order stores with a scraped email
   - 10-15 Kickstarter or Indiegogo pre-launch creators with a website and email
   - 5-10 patent holders
   - 5-15 community threads

   The run stops when it reaches `daily_target` (50).
2. **Sending at 50 a day needs a 4-6 week warm-up.** A new mailbox that sends 50 cold
   emails on day one gets flagged as spam. The scheduler ramps up automatically:
   10/day in week 1, 15 in week 2, then 20, 25, 30, 40, and 50 from week 7.
   Prospects found in the meantime wait in the queue, best fit first. To reach 50
   sooner, add a second mailbox. Don't raise the cap.

## Costs

| Item | Cost |
|---|---|
| GitHub Actions | free (about 1,400 min/month, inside the free 2,000 for private repos) |
| Web search | free tier: Brave monthly credit, Serper's 2,500 free queries, or self-hosted SearXNG. About 400 queries/month |
| Sending domain + mailbox | about $12/yr domain + $1-7/mo mailbox (Zoho Mail Lite / Google Workspace) |
| Hunter, Product Hunt, PatentsView, Reddit | free tiers, all optional |
| Claude API | Measured daily in the digest. Defaults use **Claude Opus 5.5** for every agent, roughly $5-10/day at 50 prospects. Setting `qualify_model` (and/or the others) to `claude-haiku-4-5` in `config.yaml` brings it to about $1-2/day. A good middle ground is Haiku for qualification and Opus for writing |

Without an `ANTHROPIC_API_KEY` the machine still runs. It falls back to heuristic
scoring and template emails, but the personalized audits are what make it work.

## Quick start

See **[docs/SETUP.md](docs/SETUP.md)**. In short:

1. Edit `config.yaml`: your name, booking link, **postal address** (legally required),
   and any real proof points. Nothing is sent while the placeholders are still there.
2. Add GitHub secrets: at least `LEADMACHINE_DB_KEY`, `ANTHROPIC_API_KEY`, one search
   key, and the SMTP settings.
3. Actions → lead-machine → Run workflow → `discover --no-send --target 5` for a dry
   run, then let the schedule take over.

```bash
pip install -r requirements.txt
python -m leadmachine discover --no-send --target 5   # find + qualify + draft, no email
python -m leadmachine run                             # full daily run
python -m leadmachine replies                         # inbox + follow-ups
python -m leadmachine stats | export out/leads.csv | suppress name@domain.com
pytest                                                # offline test suite
```

## Data and compliance

- Lead data (names, emails) never touches the public repo or CI logs. The SQLite
  database is gzip + AES encrypted with `LEADMACHINE_DB_KEY` and stored as a single
  force-pushed commit on the `lead-data` branch. Lead details reach you only through
  the digest email or webhook. To look at the data locally:
  `git fetch origin lead-data && git show origin/lead-data:leads.db.enc > data/leads.db.enc && python -m leadmachine decrypt`.
- Every email carries your name, postal address, an opt-out line, and a
  `List-Unsubscribe` header (CAN-SPAM). Opt-outs, "not interested" replies, and
  bounces go on a permanent suppression list. A domain is never re-pitched within
  180 days.
- Only business addresses published on the prospect's own site (or found by Hunter)
  are used. Guessing addresses is off by default.
- For EU/UK prospects, cold B2B email relies on "legitimate interest". Keep it
  relevant and one-to-one, which this pipeline does, and honor opt-outs immediately.
- The AI is instructed never to invent Cory Group results. Only the
  `agency.proof_points` you list in `config.yaml` can be claimed.

## Layout

```
leadmachine/
  sources/          Agent 1: one module per channel
  agents/enrich.py  Agent 2a: website discovery, crawl, emails, signals
  agents/qualify.py Agent 2b: ICP scoring (heuristic gate + Claude)
  agents/writer.py  Agents 3+4: audit + email sequence + LinkedIn + forum copy
  agents/scheduler.py Agent 5a: warm-up, cap, send window, follow-ups
  agents/replies.py Agent 5b: inbox triage, opt-outs, bounces, auto-reply
  pipeline.py       orchestrator; report.py digest; mailer.py SMTP/IMAP; vault.py encryption
config.yaml         all tunables; data/accelerators.yaml partnership targets
.github/workflows/lead-machine.yml   the schedule
```
