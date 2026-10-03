# Setup (about 1 hour, then hands-off)

## 1. Make the repository private (recommended)

Settings → General → Danger zone → Change visibility. Lead data is encrypted either
way, but a private repo also hides your targeting, prompts and run logs. Private repos
get 2,000 free Actions minutes/month, which is enough.

## 2. Sending mailbox (do this first: warm-up takes weeks)

Never cold-email from `corygroup.co` itself. If the domain gets flagged, your normal
email suffers too.

1. Buy a look-alike domain (about $12/yr), e.g. `corygroup-launch.co` or `trycorygroup.com`.
   Redirect its website to corygroup.co.
2. Create a mailbox on it: Zoho Mail Lite (about $1/mo) or Google Workspace (about $7/mo).
3. In the domain's DNS, set **SPF**, **DKIM** (from the mail provider) and **DMARC**
   (`v=DMARC1; p=none; rua=mailto:you@corygroup.co`).
4. Create an **app password** for SMTP/IMAP. Google needs 2FA on; Zoho has it under
   Security.
5. Optional but recommended: run a free/cheap warm-up tool on the mailbox for the first
   2-3 weeks. The machine's own warm-up ramp (`outreach.warmup_schedule`) starts at
   10/day.

## 3. Edit `config.yaml`

- `agency.sender_name`, `sender_title`, `booking_link`.
- `agency.postal_address`: a real address, required by law in commercial email.
  **Nothing is sent while the placeholder is still there.**
- `agency.proof_points`: real, verifiable results only. Leave it empty if you have none
  yet; the AI won't claim any.
- Set `outreach.auto_send: false` for the first few days if you'd like to read the
  drafts in the digest before anything goes out.

## 4. API keys (all free tiers)

| Secret | Where | Needed? |
|---|---|---|
| `LEADMACHINE_DB_KEY` | any long random passphrase (`openssl rand -base64 32`). **Save it in your password manager**; it is the only way to read the data | required |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API keys (set a monthly spend limit there) | strongly recommended |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `FROM_EMAIL` | your mailbox. Zoho: `smtp.zoho.com` / 587; Google: `smtp.gmail.com` / 587 | for sending |
| `IMAP_HOST` | `imap.zoho.com` / `imap.gmail.com` (defaults to SMTP host with smtp→imap) | for replies |
| `NOTIFY_EMAIL` | where the daily digest + CSV goes (e.g. your corygroup.co inbox) | recommended |
| `SLACK_WEBHOOK_URL` or `DISCORD_WEBHOOK_URL` | incoming webhook for digest + hot replies | optional |
| **one** of `BRAVE_API_KEY` (api-dashboard.search.brave.com), `SERPER_API_KEY` (serper.dev), `SEARXNG_URL` (self-hosted), `GOOGLE_CSE_KEY`+`GOOGLE_CSE_CX` | web search for pre-order stores, Indiegogo, website lookup | strongly recommended |
| `HUNTER_API_KEY` | hunter.io, 25 free domain searches/month: finds founders when the site only lists support@ | optional |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | reddit.com/prefs/apps → create "script" app | optional |
| `PRODUCTHUNT_TOKEN` | producthunt.com/v2/oauth/applications → developer token | optional |
| `PATENTSVIEW_API_KEY` | patentsview.org → request API key | optional |

Add them under GitHub → Settings → Secrets and variables → Actions → New repository secret.

## 5. First run

1. Actions → **lead-machine** → Run workflow → command `discover --no-send --target 5`.
   This finds, qualifies and drafts 5 prospects and sends nothing. The digest arrives
   at `NOTIFY_EMAIL` with a CSV containing every audit and email.
2. Read the drafts. Adjust `config.yaml` (categories, min score) or the prompts in
   `leadmachine/agents/writer.py` if the tone needs work.
3. From then on the schedule runs by itself:
   - Mon-Fri 14:17 UTC: full run (replies, follow-ups, discovery up to 50, sending,
     digest).
   - Every 3 hours: inbox check and due follow-ups. Interested replies get your booking
     link automatically and are flagged in Slack/Discord.

## 6. Daily routine (about 15 minutes)

- Answer the **hot replies** section of the digest (or check that the auto-reply was right).
- Post the **community engagement queue** replies by hand on Reddit/HN: helpful
  answers first, DM afterwards.
- Optionally send the LinkedIn notes for "high-fit, no email" leads.

## Running locally instead

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=... BRAVE_API_KEY=... LEADMACHINE_DB_KEY=...
python -m leadmachine discover --no-send --target 5 -v
cat out/digest-*.md
```

A cron job on any always-on machine (`python -m leadmachine run` daily, `replies` every
3 hours) works the same as GitHub Actions.

## Troubleshooting

- **0 Kickstarter leads**: Kickstarter blocks some cloud IPs. The source then falls
  back to web search automatically (needs a search key).
- **"SMTP not configured (drafts only)"** in the digest: check the SMTP secrets.
- **"outside send window"**: sending only happens 13:00-21:00 UTC
  (`outreach.send_window_utc`). The every-3-hours job picks it up.
- **Bounces climbing above 3%**: keep `allow_guessed_emails: false` and consider
  `HUNTER_API_KEY`.
