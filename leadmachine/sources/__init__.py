"""Lead sources. Each source is a generator of raw `Lead`s; failures are logged, never raised."""
from __future__ import annotations

import logging
from typing import Callable, Iterator

from ..config import Config
from ..models import Lead
from . import accelerators, hackernews, indiegogo, kickstarter, patents, preorder_stores, producthunt, reddit

log = logging.getLogger(__name__)

# Order = priority when interleaving. Pre-order stores and Kickstarter
# pre-launch pages are the highest-intent channels in the strategy.
REGISTRY: dict[str, Callable[[Config], Iterator[Lead]]] = {
    "preorder_stores": preorder_stores.fetch,
    "kickstarter_upcoming": kickstarter.fetch,
    "indiegogo": indiegogo.fetch,
    "patents": patents.fetch,
    "producthunt": producthunt.fetch,
    "hackernews": hackernews.fetch,
    "reddit": reddit.fetch,
    "accelerators": accelerators.fetch,
}


def _safe(name: str, gen: Iterator[Lead]) -> Iterator[Lead]:
    try:
        yield from gen
    except Exception:  # noqa: BLE001
        log.exception("source %s crashed; skipping it this run", name)


def interleave(cfg: Config, only: list[str] | None = None) -> Iterator[Lead]:
    """Round-robin across enabled sources so one channel can't starve the others."""
    gens = []
    for name, fn in REGISTRY.items():
        if only and name not in only:
            continue
        if not cfg.source(name).get("enabled", False):
            continue
        gens.append(_safe(name, fn(cfg)))
    while gens:
        for gen in list(gens):
            try:
                yield next(gen)
            except StopIteration:
                gens.remove(gen)
