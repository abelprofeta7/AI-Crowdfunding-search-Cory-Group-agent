"""SMTP sending and IMAP reading. Works with Google Workspace, Zoho, Outlook or any mailbox.

Env: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, FROM_EMAIL (defaults to SMTP_USER),
     IMAP_HOST (defaults to SMTP_HOST with smtp->imap), IMAP_USER/IMAP_PASSWORD (default to SMTP's).
"""
from __future__ import annotations

import email
import email.header
import imaplib
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parseaddr
from typing import Iterator

from .config import Config, env

log = logging.getLogger(__name__)


def configured() -> bool:
    return bool(env("SMTP_HOST") and env("SMTP_USER") and env("SMTP_PASSWORD"))


def placeholders_left(cfg: Config) -> bool:
    """True while config.yaml still has the example sender details (never send those)."""
    return (cfg.get_path("agency.sender_name") in (None, "", "Your Name")
            or "123 Example St" in (cfg.get_path("agency.postal_address") or "123 Example St"))


def from_email() -> str:
    return env("FROM_EMAIL") or env("SMTP_USER") or ""


def footer(cfg: Config) -> str:
    a = cfg.get_path
    lines = [a("agency.sender_name", ""), f"{a('agency.sender_title', '')}, {a('agency.name', '')}".strip(", "),
             a("agency.website", ""), "",
             f"{a('agency.postal_address', '')}",
             "Not relevant? Reply \"unsubscribe\" and I won't email again."]
    return "\n".join(lines)


def build(cfg: Config, to: str, subject: str, body: str, *, in_reply_to: str | None = None,
          with_footer: bool = True) -> EmailMessage:
    msg = EmailMessage()
    sender = from_email()
    msg["From"] = formataddr((cfg.get_path("agency.sender_name", ""), sender))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1] or None)
    msg["List-Unsubscribe"] = f"<mailto:{sender}?subject=unsubscribe>"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    text = body.strip() + ("\n\n" + footer(cfg) if with_footer else "")
    msg.set_content(text)
    return msg


class Sender:
    """Context manager holding one SMTP connection for a batch of sends."""

    def __enter__(self) -> "Sender":
        port = int(env("SMTP_PORT", "587"))
        host = env("SMTP_HOST")
        self.smtp = smtplib.SMTP_SSL(host, port, timeout=30) if port == 465 else smtplib.SMTP(host, port, timeout=30)
        if port != 465:
            self.smtp.starttls()
        self.smtp.login(env("SMTP_USER"), env("SMTP_PASSWORD"))
        return self

    def send(self, msg: EmailMessage) -> str:
        self.smtp.send_message(msg)
        return msg["Message-ID"]

    def __exit__(self, *exc) -> None:
        try:
            self.smtp.quit()
        except Exception:  # noqa: BLE001
            pass


def _body_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition")):
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                from bs4 import BeautifulSoup
                html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                return BeautifulSoup(html, "html.parser").get_text(" ")
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", "replace")


def fetch_recent(days: int = 4) -> Iterator[dict]:
    """Yields inbox messages from the last `days` days without marking them read."""
    host = env("IMAP_HOST") or (env("SMTP_HOST") or "").replace("smtp", "imap", 1)
    user, pwd = env("IMAP_USER") or env("SMTP_USER"), env("IMAP_PASSWORD") or env("SMTP_PASSWORD")
    if not (host and user and pwd):
        return
    from datetime import date, timedelta
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    imap = imaplib.IMAP4_SSL(host)
    try:
        imap.login(user, pwd)
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, "SINCE", since)
        for num in (data[0] or b"").split():
            _, parts = imap.fetch(num, "(BODY.PEEK[])")
            if not parts or not isinstance(parts[0], tuple):
                continue
            msg = email.message_from_bytes(parts[0][1])
            yield {
                "message_id": (msg.get("Message-ID") or "").strip(),
                "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
                "references": msg.get("References") or "",
                "from": parseaddr(msg.get("From") or "")[1].lower(),
                "from_name": parseaddr(msg.get("From") or "")[0],
                "to": [a for _, a in getaddresses(msg.get_all("To") or [])],
                "subject": str(email.header.make_header(email.header.decode_header(msg.get("Subject") or ""))),
                "body": _body_text(msg)[:6000],
            }
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
