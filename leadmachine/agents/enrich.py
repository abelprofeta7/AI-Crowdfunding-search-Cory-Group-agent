"""Agent 2a - Enrichment: find the prospect's website, crawl a few pages, and
extract contact emails, socials, crowdfunding history and pre-order signals.
Everything here is free (plain HTTP + DNS); Hunter.io is an optional add-on.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import http, websearch
from ..config import Config, env
from ..models import Lead, domain_of, is_platform

log = logging.getLogger(__name__)

SUBPAGES = ["/pages/contact", "/contact", "/contact-us", "/pages/about", "/about", "/about-us", "/pages/faq"]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
OBFUSCATED_RE = re.compile(r"([A-Za-z0-9._%+-]+)\s*[\[(]\s*at\s*[\])]\s*([A-Za-z0-9.-]+)\s*[\[(]\s*dot\s*[\])]\s*([A-Za-z]{2,24})", re.I)
BAD_EMAIL = ("noreply", "no-reply", "donotreply", "example.", "sentry", "wixpress", "privacy", "abuse",
             "legal@", "dmca", "u003e", "@2x", ".png", ".jpg", ".gif", ".webp", ".svg", "shopify.com",
             "domain.com", "email.com", "yourname", "jobs@", "careers@", "returns@", "billing@")
ROLE_RANK = ["founder", "ceo", "cofounder", "hello", "hi", "team", "contact", "info", "sales", "partners",
             "press", "marketing", "support", "help", "orders", "service", "customerservice", "care"]


def _clean_text(html: str, limit: int = 6000) -> tuple[str, str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    meta = soup.find("meta", attrs={"name": "description"})
    desc = (meta.get("content") or "").strip() if meta else ""
    for t in soup(["script", "style", "noscript", "svg", "iframe"]):
        t.decompose()
    text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
    return title, desc, text[:limit]


def extract_emails(html: str, domain: str | None) -> list[str]:
    found = set(m.group(0).strip(".").lower() for m in EMAIL_RE.finditer(html))
    found |= {f"{a}@{b}.{c}".lower() for a, b, c in OBFUSCATED_RE.findall(html)}
    found |= {m.lower() for m in re.findall(r"mailto:([^\"'?>\s]+)", html)}
    good = [e for e in found if not any(b in e for b in BAD_EMAIL) and len(e) < 80]
    # Prefer addresses on the prospect's own domain.
    if domain:
        own = [e for e in good if e.endswith("@" + domain) or e.endswith("." + domain)]
        if own:
            good = own
    return sorted(good, key=email_rank)


def email_rank(email: str) -> tuple[int, str]:
    local = email.split("@")[0].replace(".", "").replace("_", "").replace("-", "")
    for i, role in enumerate(ROLE_RANK):
        if local == role:
            return (i if i < 9 else 20 + i, email)
    return (5, email)  # personal-looking (e.g. jane@) ranks just below founder/ceo/hello


def detect_signals(html: str, text: str, cfg: Config) -> list[str]:
    low = html.lower()
    sig = []
    for app, needles in (cfg.get_path("signals.preorder_apps", {}) or {}).items():
        if any(n in low for n in needles):
            sig.append(f"preorder_app:{app}")
    tl = text.lower()
    phrases = [p for p in cfg.get_path("signals.preorder_phrases", []) if p in tl]
    if phrases:
        sig.append("preorder_copy:" + ",".join(phrases[:4]))
    if "cdn.shopify.com" in low or "shopify.theme" in low:
        sig.append("platform:shopify")
    elif "woocommerce" in low:
        sig.append("platform:woocommerce")
    if "kickstarter.com/projects" in low:
        sig.append("links_to_kickstarter")
    if "indiegogo.com/projects" in low:
        sig.append("links_to_indiegogo")
    if re.search(r"funded on (kickstarter|indiegogo)|as seen on kickstarter|backers? on kickstarter", tl):
        sig.append("previous_crowdfunding_success")
    return sig


def _socials(html: str) -> dict:
    out = {}
    for name, pat in (("linkedin", r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9_\-%]+"),
                      ("instagram", r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.]+"),
                      ("twitter", r"https?://(?:www\.)?(?:twitter|x)\.com/[A-Za-z0-9_]+"),
                      ("tiktok", r"https?://(?:www\.)?tiktok\.com/@[A-Za-z0-9_.]+"),
                      ("kickstarter", r"https?://(?:www\.)?kickstarter\.com/projects/[^\"'\s<>?]+"),
                      ("indiegogo", r"https?://(?:www\.)?indiegogo\.com/projects/[^\"'\s<>?]+")):
        m = re.search(pat, html)
        if m:
            out[name] = m.group(0)
    return out


def _external_links(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        if href.startswith("http") and not is_platform(domain_of(href)):
            links.append(href)
    return links


def _shopify_products(base: str) -> list[dict]:
    resp = http.get(base.rstrip("/") + "/products.json?limit=50", respect_robots=False, timeout=12)
    if resp is None or resp.status_code != 200:
        return []
    try:
        products = resp.json().get("products", [])
    except ValueError:
        return []
    out = []
    for p in products:
        tags = " ".join(p.get("tags") if isinstance(p.get("tags"), list) else [str(p.get("tags", ""))]).lower()
        title = p.get("title", "")
        variants = p.get("variants") or [{}]
        pre = "pre" in tags and "order" in tags or "pre-order" in title.lower() or "preorder" in title.lower()
        out.append({"title": title, "price": variants[0].get("price"), "preorder": pre,
                    "handle": p.get("handle")})
    return out


def find_website(lead: Lead) -> str | None:
    """For leads that only have a platform URL or a company name."""
    if lead.extra.get("redirect"):
        resp = http.get(lead.extra["redirect"], respect_robots=False, timeout=15)
        if resp is not None and resp.ok and not is_platform(domain_of(resp.url)):
            return f"https://{domain_of(resp.url)}"
    if lead.extra.get("platform") in ("kickstarter", "indiegogo"):
        resp = http.get(lead.source_url, respect_robots=False, headers={"User-Agent": http.BROWSER_UA})
        if resp is not None and resp.ok:
            links = _external_links(resp.text, lead.source_url)
            if links:
                return f"https://{domain_of(links[0])}"
    if websearch.available() and lead.stage in ("patent", "prelaunch") and (lead.company or lead.contact_name):
        q = f'"{lead.company}" official site' if lead.company else f'"{lead.contact_name}" "{lead.name}"'
        for r in websearch.search(q, n=5):
            d = domain_of(r.get("url"))
            if d and not is_platform(d):
                return f"https://{d}"
    return None


def hunter_lookup(domain: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Optional Hunter.io domain search (25 free searches/month). -> (email, name, role, linkedin)"""
    key = env("HUNTER_API_KEY")
    if not key:
        return None, None, None, None
    data = http.get_json("https://api.hunter.io/v2/domain-search",
                         params={"domain": domain, "api_key": key, "limit": 10})
    emails = ((data or {}).get("data") or {}).get("emails") or []
    def score(e):  # decision makers first
        pos = (e.get("position") or "").lower()
        return (0 if any(k in pos for k in ("founder", "ceo", "owner", "president")) else
                1 if any(k in pos for k in ("marketing", "growth", "product")) else 2, -(e.get("confidence") or 0))
    for e in sorted(emails, key=score):
        if (e.get("confidence") or 0) >= 80:
            name = " ".join(x for x in (e.get("first_name"), e.get("last_name")) if x) or None
            return e.get("value"), name, e.get("position"), e.get("linkedin")
    return None, None, None, None


def has_mx(domain: str) -> bool:
    try:
        import dns.resolver
        return bool(dns.resolver.resolve(domain, "MX", lifetime=8))
    except Exception:  # noqa: BLE001
        return False


def enrich(lead: Lead, cfg: Config) -> Lead:
    if not lead.domain:
        site = find_website(lead)
        if site:
            lead.website = site
    lead.extra.setdefault("pages", {})
    if not lead.domain:
        return lead

    base = f"https://{lead.domain}"
    resp = http.get(base, headers={"User-Agent": http.BROWSER_UA}, respect_robots=False)
    if resp is None or resp.status_code >= 400:
        lead.extra["site_error"] = getattr(resp, "status_code", "unreachable")
        return lead
    html_all = resp.text
    title, desc, text = _clean_text(resp.text)
    lead.extra["site_title"], lead.extra["site_description"] = title, desc
    texts = [text]

    pages = [lead.extra["product_url"]] if lead.extra.get("product_url", "").startswith(base) else []
    for path in pages + SUBPAGES:
        url = path if path.startswith("http") else base + path
        r = http.get(url, headers={"User-Agent": http.BROWSER_UA})
        if r is not None and r.ok and len(r.text) > 500:
            html_all += r.text
            texts.append(_clean_text(r.text, 3000)[2])
            if len(texts) >= 4:
                break

    lead.extra["page_text"] = " | ".join(texts)[:9000]
    lead.signals = sorted(set(lead.signals) | set(detect_signals(html_all, " ".join(texts), cfg)))
    socials = _socials(html_all)
    lead.extra["socials"] = socials
    lead.linkedin = lead.linkedin or socials.get("linkedin")

    if any(s.startswith("platform:shopify") for s in lead.signals):
        products = _shopify_products(base)
        pre = [p for p in products if p["preorder"]]
        if pre:
            lead.signals.append("shopify_preorder_products")
        lead.extra["products"] = (pre or products)[:8]
        if not lead.price and (pre or products):
            lead.price = (pre or products)[0].get("price")

    emails = extract_emails(html_all, lead.domain)
    lead.extra["emails_found"] = emails[:6]
    if emails:
        lead.email, lead.email_source = emails[0], "scraped"
    if (not lead.email or email_rank(lead.email)[0] >= 9) and lead.domain:
        e, name, role, li = hunter_lookup(lead.domain)
        if e:
            lead.email, lead.email_source = e, "hunter"
            lead.contact_name = name or lead.contact_name
            lead.contact_role = role or lead.contact_role
            lead.linkedin = li or lead.linkedin
    if not lead.email and cfg.get_path("outreach.allow_guessed_emails", False) and has_mx(lead.domain):
        lead.email, lead.email_source = f"hello@{lead.domain}", "pattern"
    return lead


def summary_for_llm(lead: Lead) -> str:
    """Compact JSON view of a lead for the agents' prompts."""
    view = {k: v for k, v in lead.to_dict().items() if k not in ("extra", "id") and v}
    extra = {k: lead.extra.get(k) for k in ("goal", "currency", "site_title", "site_description",
                                             "products", "socials", "crowdfunding_links", "patent_date",
                                             "subreddit", "points", "score") if lead.extra.get(k)}
    view["extra"] = extra
    view["page_text_excerpt"] = (lead.extra.get("page_text") or "")[:6000]
    return json.dumps(view, ensure_ascii=False, indent=1)
