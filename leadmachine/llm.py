"""Thin wrapper around the Claude API for structured (pydantic) agent outputs.

Every agent asks Claude for JSON matching a pydantic model, so downstream code
never parses free text. If no ANTHROPIC_API_KEY is configured the machine still
runs: agents fall back to heuristics and templates.
"""
from __future__ import annotations

import logging
import time
from typing import TypeVar

from pydantic import BaseModel

from .config import env

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

# Models that accept output_config.effort / server-side refusal fallbacks.
_NO_EFFORT = ("claude-haiku",)
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")

# $ per million tokens (input, output), for the daily cost line in the digest.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


class LLM:
    def __init__(self) -> None:
        self.enabled = bool(env("ANTHROPIC_API_KEY") or env("ANTHROPIC_AUTH_TOKEN"))
        self.client = None
        self.usage: dict[str, list[int]] = {}
        if self.enabled:
            import anthropic

            self.client = anthropic.Anthropic(max_retries=3, timeout=180)

    def structured(self, *, model: str, system: str, user: str, schema: type[T],
                   effort: str | None = None, max_tokens: int = 16000) -> T | None:
        """One Claude call returning a validated `schema` instance, or None on failure."""
        if not self.enabled:
            return None
        import anthropic

        kwargs: dict = dict(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}], output_format=schema,
        )
        if effort and not model.startswith(_NO_EFFORT):
            kwargs["output_config"] = {"effort": effort}
        if model in _FALLBACK_MODELS:
            # On a safety-classifier decline, re-run on Anthropic's recommended model.
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"

        for attempt in range(2):
            try:
                resp = self.client.beta.messages.parse(**kwargs)
            except anthropic.RateLimitError as exc:
                wait = int(exc.response.headers.get("retry-after", "30"))
                log.warning("Claude rate limited; sleeping %ss", wait)
                time.sleep(min(wait, 120))
                continue
            except anthropic.BadRequestError as exc:
                log.error("Claude rejected request (%s): %s", model, exc.message)
                return None
            except anthropic.APIStatusError as exc:
                log.error("Claude API error %s: %s", exc.status_code, exc.message)
                return None
            except anthropic.APIConnectionError as exc:
                log.error("Claude connection error: %s", exc)
                return None
            self._track(model, resp.usage)
            if resp.stop_reason == "refusal":
                log.warning("Claude declined the request (model %s)", model)
                return None
            if resp.stop_reason == "max_tokens":
                log.warning("Claude output truncated (model %s)", model)
                return None
            return resp.parsed_output
        return None

    def _track(self, model: str, usage) -> None:
        tin = (usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        bucket = self.usage.setdefault(model, [0, 0])
        bucket[0] += tin
        bucket[1] += usage.output_tokens or 0

    def cost_usd(self) -> float:
        total = 0.0
        for model, (tin, tout) in self.usage.items():
            pin, pout = PRICES.get(model, (4.0, 20.0))
            total += tin / 1e6 * pin + tout / 1e6 * pout
        return round(total, 2)
