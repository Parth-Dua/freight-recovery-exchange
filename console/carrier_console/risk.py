"""Simulated maritime risk events.

Canonical shape follows docs/specs/05-disruption-detection.md section 5, so a later
real UKMTO/MARAD adapter can populate the same fields.

AT-05-01: loading a risk event must never withdraw anything. Nothing in this module
writes to Tiger. A risk event is context a carrier operator reads before deciding;
the withdrawal is always an explicit, separately authorized act.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "risk_events.json"


@dataclass(frozen=True)
class RiskEvent:
    risk_event_id: str
    source_kind: str
    source_provider: str
    source_reference: str
    source_url: str | None
    published_at: datetime
    category: str
    severity: str
    description: str
    region_code: str
    affected_corridors: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{self.severity.upper()} · {self.category} · {self.region_code}"


def _parse(raw: dict) -> RiskEvent:
    return RiskEvent(
        risk_event_id=raw["risk_event_id"],
        source_kind=raw["source_kind"],
        source_provider=raw["source_provider"],
        source_reference=raw["source_reference"],
        source_url=raw.get("source_url"),
        published_at=datetime.fromisoformat(raw["published_at"]),
        category=raw["category"],
        severity=raw["severity"],
        description=raw["description"],
        region_code=raw["region_code"],
        affected_corridors=tuple(raw.get("affected_corridors", [])),
    )


@lru_cache(maxsize=1)
def load_risk_events() -> tuple[RiskEvent, ...]:
    with FIXTURES.open() as handle:
        return tuple(_parse(item) for item in json.load(handle))


def get_risk_event(risk_event_id: str) -> RiskEvent | None:
    for event in load_risk_events():
        if event.risk_event_id == risk_event_id:
            return event
    return None
