"""Lead record passed between the agents."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host or None


# Hosts that are platforms, not a prospect's own website.
PLATFORM_DOMAINS = {
    "kickstarter.com", "indiegogo.com", "backerkit.com", "launchboom.com", "producthunt.com",
    "reddit.com", "news.ycombinator.com", "youtube.com", "youtu.be", "instagram.com",
    "facebook.com", "twitter.com", "x.com", "tiktok.com", "linkedin.com", "amazon.com",
    "github.com", "medium.com", "imgur.com", "i.redd.it", "v.redd.it", "linktr.ee",
    "kck.st", "igg.me", "bit.ly", "google.com", "patents.google.com", "etsy.com",
    "shopify.com", "apps.shopify.com", "discord.gg", "discord.com", "wikipedia.org",
}


def is_platform(domain: str | None) -> bool:
    if not domain:
        return True
    return any(domain == d or domain.endswith("." + d) for d in PLATFORM_DOMAINS)


@dataclass
class Lead:
    source: str                      # e.g. "kickstarter_upcoming"
    source_url: str                  # where we found it
    name: str                        # product / project / company name
    stage: str = "unknown"           # prelaunch | preorder | repeat_creator | patent | forum | partner
    company: str | None = None
    website: str | None = None
    contact_name: str | None = None
    contact_role: str | None = None
    email: str | None = None
    email_source: str | None = None  # scraped | hunter | pattern | role
    linkedin: str | None = None
    category: str | None = None
    price: str | None = None
    location: str | None = None
    description: str | None = None
    signals: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    id: int | None = None

    @property
    def domain(self) -> str | None:
        d = domain_of(self.website)
        return None if is_platform(d) else d

    @property
    def key(self) -> str:
        """Dedup key: own domain if known, else the source URL."""
        return self.domain or self.source_url.lower().rstrip("/")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
