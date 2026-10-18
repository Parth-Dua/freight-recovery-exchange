"""A carrier's pricing-agent settings: read, validate, save.

The policy lives in carrier_policies.policy (JSON) and the carrier agent rereads it on every
quote, so a save here changes the agent's behavior on its next message. It is private to the
carrier: the exchange never reads it, and the market_events row a save writes names the
changed fields only, never their values.

Keys the carrier agent does not understand yet are ignored by Policy.from_json, so the new
fields from the design doc (section 8) can be saved before the agent applies them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from carrier_console.db import emit_event

SELECTIONS = ("earliest_arrival", "earliest_departure")
GOALS = ("fill_ship", "balanced", "protect_margin")

# What each business goal preset sets. The operator can adjust any of them afterwards.
PRESETS: dict[str, dict[str, int]] = {
    "fill_ship": {
        "utilization_premium_pct": 6,
        "concession_empty_pct": 12,
        "concession_full_pct": 4,
        "disruption_surge_pct": 8,
    },
    "balanced": {
        "utilization_premium_pct": 10,
        "concession_empty_pct": 8,
        "concession_full_pct": 2,
        "disruption_surge_pct": 15,
    },
    "protect_margin": {
        "utilization_premium_pct": 15,
        "concession_empty_pct": 4,
        "concession_full_pct": 0,
        "disruption_surge_pct": 25,
    },
}


@dataclass(frozen=True)
class Preferences:
    """The fields the preferences page edits. Money is in minor units (cents)."""

    business_goal: str
    target_price_per_unit_minor: int
    min_price_per_unit_minor: int
    utilization_premium_pct: int
    concession_empty_pct: int
    concession_full_pct: int
    disruption_surge_pct: int
    offer_ttl_seconds: int
    max_hold_seconds: int
    selection: str
    payment_terms: str


@dataclass(frozen=True)
class StoredPolicy:
    carrier_id: str
    display_name: str
    version: int
    updated_at: datetime
    policy: dict[str, Any]

    def preferences(self) -> Preferences:
        return from_policy(self.policy)


class PreferencesInvalid(ValueError):
    """Field name to message, for showing next to each field."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


class VersionConflict(RuntimeError):
    """Someone else saved since this form was loaded."""


def from_policy(policy: dict[str, Any]) -> Preferences:
    """Today's stored policies have one fixed concession_pct; show it as both ends until the
    operator sets them apart."""
    concession = int(policy.get("concession_pct", 0))
    return Preferences(
        business_goal=policy.get("business_goal", "balanced"),
        target_price_per_unit_minor=int(policy["target_price_per_unit_minor"]),
        min_price_per_unit_minor=int(policy["min_price_per_unit_minor"]),
        utilization_premium_pct=int(policy.get("utilization_premium_pct", 0)),
        concession_empty_pct=int(policy.get("concession_empty_pct", concession)),
        concession_full_pct=int(policy.get("concession_full_pct", concession)),
        disruption_surge_pct=int(policy.get("disruption_surge_pct", 0)),
        offer_ttl_seconds=int(policy.get("offer_ttl_seconds", 600)),
        max_hold_seconds=int(policy.get("max_hold_seconds", 3600)),
        selection=policy.get("selection", "earliest_arrival"),
        payment_terms=policy.get("payment_terms", ""),
    )


def apply_preset(prefs: Preferences, goal: str) -> Preferences:
    if goal not in PRESETS:
        raise PreferencesInvalid({"business_goal": "Pick one of the three goals."})
    return Preferences(**{**asdict(prefs), **PRESETS[goal], "business_goal": goal})


def validate(prefs: Preferences) -> None:
    errors: dict[str, str] = {}

    def pct(name: str, value: int, top: int = 100) -> None:
        if not 0 <= value <= top:
            errors[name] = f"Must be between 0 and {top}%."

    if prefs.business_goal not in GOALS:
        errors["business_goal"] = "Pick one of the three goals."
    if prefs.min_price_per_unit_minor <= 0:
        errors["min_price_per_unit_minor"] = "Floor must be above $0."
    if prefs.target_price_per_unit_minor < prefs.min_price_per_unit_minor:
        errors["target_price_per_unit_minor"] = "Target must be at or above the floor price."
    pct("utilization_premium_pct", prefs.utilization_premium_pct)
    pct("concession_empty_pct", prefs.concession_empty_pct, 50)
    pct("concession_full_pct", prefs.concession_full_pct, 50)
    pct("disruption_surge_pct", prefs.disruption_surge_pct)
    if prefs.concession_full_pct > prefs.concession_empty_pct:
        errors["concession_full_pct"] = "A full ship should not discount more than an empty one."
    if not 60 <= prefs.offer_ttl_seconds <= 86_400:
        errors["offer_ttl_seconds"] = "Between 1 minute and 24 hours."
    if not 300 <= prefs.max_hold_seconds <= 86_400:
        errors["max_hold_seconds"] = "Between 5 minutes and 24 hours."
    if prefs.selection not in SELECTIONS:
        errors["selection"] = "Earliest arrival or earliest departure."
    if not prefs.payment_terms.strip() or len(prefs.payment_terms) > 200:
        errors["payment_terms"] = "Required, up to 200 characters."
    if errors:
        raise PreferencesInvalid(errors)


def merged_policy(stored: dict[str, Any], prefs: Preferences) -> dict[str, Any]:
    """Keep every key we do not edit (currency, anything Parth adds later). Also keep the
    legacy concession_pct meaningful for carrier code that has not moved to the two-ended
    concession yet: the midpoint, the discount at a half-full ship."""
    policy = {**stored, **asdict(prefs)}
    policy["payment_terms"] = prefs.payment_terms.strip()
    policy["concession_pct"] = round((prefs.concession_empty_pct + prefs.concession_full_pct) / 2)
    return policy


def changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(k for k in after if before.get(k) != after[k])


def preview(prefs: Preferences, capacity: int = 4, available: int = 2) -> tuple[int, int]:
    """(opening, lowest accepted) in minor units for one container, using the design doc's
    formula (section 9). For the page's "with these settings" line only; the carrier agent
    owns the real pricing."""
    utilization = 1 - available / capacity
    opening = prefs.target_price_per_unit_minor * (1 + prefs.utilization_premium_pct * utilization / 100)
    concession = prefs.concession_empty_pct + (prefs.concession_full_pct - prefs.concession_empty_pct) * utilization
    lowest = max(prefs.min_price_per_unit_minor, opening * (1 - concession / 100))
    return round(opening), round(lowest)


LOAD_SQL = """
SELECT p.carrier_id, c.display_name, p.version, p.updated_at, p.policy
FROM carrier_policies p JOIN carriers c ON c.carrier_id = p.carrier_id
WHERE p.carrier_id = %s
"""


async def list_carriers(pool: AsyncConnectionPool) -> list[tuple[str, str]]:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute("SELECT carrier_id, display_name FROM carriers WHERE active ORDER BY display_name")
        ).fetchall()
    return [(row[0], row[1]) for row in rows]


async def load_policy(pool: AsyncConnectionPool, carrier_id: str) -> StoredPolicy | None:
    async with pool.connection() as conn:
        row = await (await conn.execute(LOAD_SQL, (carrier_id,))).fetchone()
    return StoredPolicy(*row) if row else None


async def save_policy(
    pool: AsyncConnectionPool,
    carrier_id: str,
    prefs: Preferences,
    *,
    expected_version: int,
    actor_id: str,
) -> StoredPolicy:
    """Validate, merge and save in one transaction. Fails with VersionConflict if the policy
    changed since the form loaded, so two operators cannot silently overwrite each other."""
    validate(prefs)
    async with pool.connection() as conn, conn.transaction():
        row = await (
            await conn.execute(
                "SELECT version, policy FROM carrier_policies WHERE carrier_id = %s FOR UPDATE",
                (carrier_id,),
            )
        ).fetchone()
        if row is None:
            raise LookupError(f"no policy for carrier {carrier_id}")
        version, stored = row
        if version != expected_version:
            raise VersionConflict(f"policy is at version {version}, form was loaded at {expected_version}")
        policy = merged_policy(stored, prefs)
        changed = changed_fields(stored, policy)
        if changed:
            version += 1
            await conn.execute(
                "UPDATE carrier_policies SET policy = %s, version = %s, updated_at = now() WHERE carrier_id = %s",
                (Jsonb(policy), version, carrier_id),
            )
            await emit_event(
                conn,
                "CARRIER_POLICY_UPDATED",
                "carrier_policy",
                carrier_id,
                carrier_id=carrier_id,
                payload={"version": version, "changed_fields": changed, "actor_id": actor_id},
            )
    stored_policy = await load_policy(pool, carrier_id)
    assert stored_policy is not None
    return stored_policy


FORM_FIELDS = [f.name for f in fields(Preferences)]
