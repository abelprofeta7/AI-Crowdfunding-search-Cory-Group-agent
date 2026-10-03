"""Hardware incubators/accelerators: partnership leads (masterclass offer), from data/accelerators.yaml."""
from __future__ import annotations

from typing import Iterator

import yaml

from ..config import ROOT, Config
from ..models import Lead


def fetch(cfg: Config) -> Iterator[Lead]:
    path = ROOT / "data" / "accelerators.yaml"
    if not path.exists():
        return
    for a in yaml.safe_load(path.read_text()) or []:
        yield Lead(source="accelerators", source_url=a["website"], name=a["name"], company=a["name"],
                   stage="partner", website=a["website"], location=a.get("location"),
                   category=a.get("focus"), signals=["hardware_accelerator"],
                   contact_role="Program Director")
