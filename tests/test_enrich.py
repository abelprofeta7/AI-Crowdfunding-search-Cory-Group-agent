from pathlib import Path

from leadmachine.agents import enrich
from leadmachine.config import load_config

HTML = (Path(__file__).parent / "fixtures" / "preorder_store.html").read_text()
cfg = load_config()


def test_extract_emails_prefers_founder_and_drops_junk():
    emails = enrich.extract_emails(HTML, "skydesk.example")
    assert emails[0] == "founder@skydesk.example"
    assert "hello@skydesk.example" in emails
    assert not any("noreply" in e or "@2x" in e for e in emails)


def test_detect_signals_finds_preorder_app_and_shopify():
    sig = enrich.detect_signals(HTML, "Pre-order now. Ships in March.", cfg)
    assert "preorder_app:purple_dot" in sig
    assert "platform:shopify" in sig
    assert any(s.startswith("preorder_copy:") for s in sig)


def test_socials():
    s = enrich._socials(HTML)
    assert s["linkedin"].endswith("/company/skydesk")
    assert "instagram" in s


def test_enrich_end_to_end_with_fake_http(monkeypatch):
    class R:
        def __init__(self, text, status=200, url="https://skydesk.example"):
            self.text, self.status_code, self.url, self.ok = text, status, url, status < 400

        def json(self):
            return {"products": [{"title": "SkyDesk Radar (Pre-Order)", "tags": ["pre-order"],
                                  "variants": [{"price": "299.00"}], "handle": "radar"}]}

    def fake_get(url, **kw):
        if url.endswith("products.json?limit=50"):
            return R("{}")
        if url == "https://skydesk.example":
            return R(HTML)
        return R("", 404)

    monkeypatch.setattr(enrich.http, "get", fake_get)
    from leadmachine.models import Lead
    lead = enrich.enrich(Lead(source="preorder_stores", source_url="https://skydesk.example/x",
                              name="SkyDesk", stage="preorder", website="https://skydesk.example"), cfg)
    assert lead.email == "founder@skydesk.example" and lead.email_source == "scraped"
    assert "shopify_preorder_products" in lead.signals
    assert lead.price == "299.00"
    assert "Ships in March" in lead.extra["page_text"]
