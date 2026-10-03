"""Delivers the daily digest: email to NOTIFY_EMAIL (with CSV attached) and/or a
Slack/Discord webhook (SLACK_WEBHOOK_URL / DISCORD_WEBHOOK_URL)."""
from __future__ import annotations

import logging

from . import http, mailer
from .config import Config, env

log = logging.getLogger(__name__)


def webhook(text: str) -> None:
    for var, key in (("SLACK_WEBHOOK_URL", "text"), ("DISCORD_WEBHOOK_URL", "content")):
        url = env(var)
        if not url:
            continue
        try:
            limit = 1900 if key == "content" else 3500
            http.SESSION.post(url, json={key: text[:limit]}, timeout=15)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s post failed: %s", var, exc)


def email_digest(cfg: Config, subject: str, markdown: str, csv_text: str | None) -> None:
    to = env("NOTIFY_EMAIL")
    if not (to and mailer.configured()):
        return
    msg = mailer.build(cfg, to, subject, markdown, with_footer=False)
    del msg["List-Unsubscribe"]
    if csv_text:
        msg.add_attachment(csv_text.encode(), maintype="text", subtype="csv", filename="prospects.csv")
    try:
        with mailer.Sender() as s:
            s.send(msg)
    except Exception as exc:  # noqa: BLE001
        log.error("digest email failed: %s", exc)
