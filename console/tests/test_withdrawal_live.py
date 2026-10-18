"""Live Tiger integration tests. Skipped unless TIGER_DATABASE_URL is set.

These are the ones that count as phase 5 evidence. Run them against the seeded
demo market from the repo root:  uv run python scripts/db.py migrate && ... seed
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from carrier_console.config import load_env
from carrier_console.db import close_pools, get_pool
from carrier_console.queries import list_sailings
from carrier_console.withdrawal import (
    RejectionCode,
    WithdrawalRejected,
    WithdrawSailing,
    withdraw_sailing,
)

load_env()

pytestmark = pytest.mark.skipif(
    not os.getenv("TIGER_DATABASE_URL"),
    reason="TIGER_DATABASE_URL not set; this suite requires live Tiger",
)


def _command(sailing_id: str, carrier_id: str) -> WithdrawSailing:
    return WithdrawSailing(
        sailing_id=sailing_id,
        carrier_id=carrier_id,
        reason_risk_event_id="risk_sim_0001",
        actor_id=f"operator:{carrier_id}",
        authorization_ref="SIM-ADV-2026-014",
        idempotency_key=uuid4().hex,
        reason_text="pytest integration run",
    )


@pytest.fixture(autouse=True)
async def _cleanup():
    """Close this test's pool inside this test's event loop. pytest-asyncio gives each
    test its own loop, so closing them all at module teardown hit already-closed loops."""
    yield
    await close_pools()


async def test_unknown_sailing_is_rejected() -> None:
    pool = await get_pool()
    with pytest.raises(WithdrawalRejected) as caught:
        await withdraw_sailing(pool, _command("NOPE-999", "atlas"))
    assert caught.value.code is RejectionCode.UNKNOWN_SAILING


async def test_carrier_cannot_withdraw_another_carriers_sailing() -> None:
    """AT-05-04."""
    pool = await get_pool()
    sailings = await list_sailings(pool)
    assert sailings, "seed the demo market first"
    target = sailings[0]
    impostor = next(s.carrier_id for s in sailings if s.carrier_id != target.carrier_id)

    with pytest.raises(WithdrawalRejected) as caught:
        await withdraw_sailing(pool, _command(target.sailing_id, impostor))
    assert caught.value.code is RejectionCode.CROSS_CARRIER_WITHDRAWAL


async def test_withdrawal_is_idempotent() -> None:
    """AT-05-03: the second call changes nothing and reuses the first event id."""
    pool = await get_pool()
    active = [s for s in await list_sailings(pool) if s.is_active]
    if not active:
        pytest.skip("no ACTIVE sailing left to withdraw; re-seed to run this test")
    target = active[-1]

    first = await withdraw_sailing(pool, _command(target.sailing_id, target.carrier_id))
    assert first.already_withdrawn is False

    second = await withdraw_sailing(pool, _command(target.sailing_id, target.carrier_id))
    assert second.already_withdrawn is True
    assert second.event_id == first.event_id
    assert second.disrupted_booking_ids == ()


async def test_other_sailings_keep_their_status() -> None:
    """AT-05-02."""
    pool = await get_pool()
    before = {s.sailing_id: s.status for s in await list_sailings(pool)}
    active = [s for s in await list_sailings(pool) if s.is_active]
    if not active:
        pytest.skip("no ACTIVE sailing left to withdraw; re-seed to run this test")
    target = active[0]

    await withdraw_sailing(pool, _command(target.sailing_id, target.carrier_id))

    after = {s.sailing_id: s.status for s in await list_sailings(pool)}
    for sailing_id, status in before.items():
        if sailing_id != target.sailing_id:
            assert after[sailing_id] == status
