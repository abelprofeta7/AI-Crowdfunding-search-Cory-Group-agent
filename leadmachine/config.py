"""Loads config.yaml and exposes environment secrets."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


class Config(dict):
    """Dict with dotted lookup: cfg.get_path("sources.reddit.enabled")."""

    def get_path(self, path: str, default: Any = None) -> Any:
        node: Any = self
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def source(self, name: str) -> dict:
        return self.get_path(f"sources.{name}", {}) or {}


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path or os.environ.get("LEADMACHINE_CONFIG") or ROOT / "config.yaml")
    with open(path, encoding="utf-8") as fh:
        return Config(yaml.safe_load(fh) or {})


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value if value else default
