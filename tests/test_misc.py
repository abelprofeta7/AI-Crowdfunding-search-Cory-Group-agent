from types import SimpleNamespace

from pydantic import BaseModel

from leadmachine import vault
from leadmachine.agents import replies
from leadmachine.llm import LLM


def test_vault_roundtrip(tmp_path):
    src, enc, back = tmp_path / "a.db", tmp_path / "a.enc", tmp_path / "b.db"
    src.write_bytes(b"secret lead data")
    vault.encrypt(src, enc, "pass")
    assert b"secret" not in enc.read_bytes()
    vault.decrypt(enc, back, "pass")
    assert back.read_bytes() == b"secret lead data"


def test_keyword_intent():
    assert replies._keyword_intent("Please unsubscribe me") == "unsubscribe"
    assert replies._keyword_intent("I'm out of office until Monday") == "out_of_office"
    assert replies._keyword_intent("Sure, send it over!") == "interested"
    assert replies._keyword_intent("No thanks") == "not_interested"


def test_strip_quoted():
    body = "Sounds good, send it.\n\nOn Mon, Oct 5, 2026 at 9:00 AM Jo wrote:\n> Hi there"
    assert replies._strip_quoted(body) == "Sounds good, send it."


class Out(BaseModel):
    ok: bool


def test_llm_request_shape():
    calls = []

    class Msgs:
        def parse(self, **kw):
            calls.append(kw)
            return SimpleNamespace(stop_reason="end_turn", parsed_output=Out(ok=True),
                                   usage=SimpleNamespace(input_tokens=1000, output_tokens=100,
                                                         cache_read_input_tokens=0))

    llm = LLM()
    llm.enabled = True
    llm.client = SimpleNamespace(beta=SimpleNamespace(messages=Msgs()))
    assert llm.structured(model="claude-opus-5-5", system="s", user="u", schema=Out, effort="low").ok
    kw = calls[0]
    assert kw["output_format"] is Out and kw["output_config"] == {"effort": "low"}
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    llm.structured(model="claude-haiku-4-5", system="s", user="u", schema=Out, effort="low")
    assert "output_config" not in calls[1] and "fallbacks" not in calls[1]
    assert llm.cost_usd() == round(1000 / 1e6 * 4 + 100 / 1e6 * 20 + 1000 / 1e6 * 1 + 100 / 1e6 * 5, 2)
