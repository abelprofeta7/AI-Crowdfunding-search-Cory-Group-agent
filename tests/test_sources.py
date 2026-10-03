from leadmachine.config import Config
from leadmachine.models import Lead, domain_of, is_platform
from leadmachine.sources import interleave, kickstarter, reddit


def test_domain_and_platform():
    assert domain_of("https://www.Example.com/x") == "example.com"
    assert is_platform("kickstarter.com") and is_platform("shop.kickstarter.com")
    assert not is_platform("skydesk.example")
    lead = Lead(source="s", source_url="https://www.kickstarter.com/projects/a/b", name="n",
                website="https://kickstarter.com/x")
    assert lead.domain is None and lead.key == "https://www.kickstarter.com/projects/a/b"


def test_parse_kickstarter_project():
    p = {"name": "Orbit Lamp", "blurb": "A levitating lamp", "goal": 20000, "currency": "USD",
         "creator": {"name": "Ana Ruiz", "urls": {"web": {"user": "https://www.kickstarter.com/profile/ana"}}},
         "category": {"name": "Gadgets", "parent_name": "Technology"},
         "location": {"displayable_name": "Austin, TX"},
         "urls": {"web": {"project": "https://www.kickstarter.com/projects/ana/orbit-lamp?ref=discovery"}}}
    lead = kickstarter.parse_project(p)
    assert lead.source_url == "https://www.kickstarter.com/projects/ana/orbit-lamp"
    assert lead.stage == "prelaunch" and lead.contact_name == "Ana Ruiz"
    assert lead.category == "Technology / Gadgets"


def test_parse_reddit_post_finds_own_site():
    d = {"title": "Launching our tabletop game on Kickstarter next month", "author": "dice_guy",
         "permalink": "/r/kickstarter/comments/abc/x/", "subreddit": "kickstarter",
         "selftext": "Prelaunch page: https://www.kickstarter.com/projects/dice/game and site https://dicegame.example.",
         "url": "https://www.reddit.com/r/kickstarter/comments/abc/x/"}
    lead = reddit.parse_post(d)
    assert lead.website == "https://dicegame.example"
    assert lead.extra["crowdfunding_links"] == ["https://www.kickstarter.com/projects/dice/game"]


def test_interleave_round_robin(monkeypatch):
    from leadmachine import sources
    def mk(prefix):
        return lambda cfg: (Lead(source=prefix, source_url=f"{prefix}{i}", name=str(i)) for i in range(3))
    monkeypatch.setattr(sources, "REGISTRY", {"a": mk("a"), "b": mk("b")})
    cfg = Config({"sources": {"a": {"enabled": True}, "b": {"enabled": True}}})
    assert [l.source_url for l in interleave(cfg)] == ["a0", "b0", "a1", "b1", "a2", "b2"]
