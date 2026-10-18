"""Live Tiger tests for saving preferences. Local databases only; every test restores the
policy it changed."""

from __future__ import annotations

import os
from dataclasses import replace
from urllib.parse import urlparse

import pytest
from psycopg.types.json import Jsonb

from carrier_console.config import load_env
from carrier_console.db import close_pools, get_pool
from carrier_console.preferences import VersionConflict, load_policy, save_policy

load_env()

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

pytestmark = pytest.mark.skipif(
    urlparse(os.getenv("TIGER_DATABASE_URL") or "").hostname not in LOCAL_HOSTS,
    reason="TIGER_DATABASE_URL is unset or not local; these tests write carrier policies",
)


@pytest.fixture
async def pool():
    pool = await get_pool()
    yield pool
    await close_pools()


@pytest.fixture
async def atlas(pool):
    """Atlas's stored policy, restored exactly after the test."""
    original = await load_policy(pool, "atlas")
    if original is None:
        pytest.skip("no atlas policy; run dev_db.py reset")
    yield original
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE carrier_policies SET policy = %s, version = %s WHERE carrier_id = 'atlas'",
            (Jsonb(original.policy), original.version),
        )


async def test_save_bumps_version_and_records_field_names_only(pool, atlas) -> None:
    prefs = replace(atlas.preferences(), disruption_surge_pct=17)
    saved = await save_policy(pool, "atlas", prefs, expected_version=atlas.version, actor_id="pytest")

    assert saved.version == atlas.version + 1
    assert saved.policy["disruption_surge_pct"] == 17
    assert saved.policy["currency"] == atlas.policy["currency"]
    async with pool.connection() as conn:
        payload = (await (await conn.execute(
            """SELECT payload FROM market_events
               WHERE event_type = 'CARRIER_POLICY_UPDATED' AND entity_id = 'atlas'
               ORDER BY ts DESC LIMIT 1"""
        )).fetchone())[0]
    assert "disruption_surge_pct" in payload["changed_fields"]
    assert "17" not in str(payload)  # the value itself never leaves the policy table


async def test_stale_form_cannot_overwrite_a_newer_save(pool, atlas) -> None:
    await save_policy(pool, "atlas", replace(atlas.preferences(), disruption_surge_pct=9),
                      expected_version=atlas.version, actor_id="pytest")
    with pytest.raises(VersionConflict):
        await save_policy(pool, "atlas", replace(atlas.preferences(), disruption_surge_pct=30),
                          expected_version=atlas.version, actor_id="pytest")
    assert (await load_policy(pool, "atlas")).policy["disruption_surge_pct"] == 9


async def test_saving_unchanged_settings_is_a_no_op(pool, atlas) -> None:
    first = await save_policy(pool, "atlas", atlas.preferences(), expected_version=atlas.version,
                              actor_id="pytest")
    again = await save_policy(pool, "atlas", first.preferences(), expected_version=first.version,
                              actor_id="pytest")
    assert again.version == first.version
