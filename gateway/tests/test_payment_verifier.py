"""Ledger proof predicate (unit, fake ledger). Real on-chain evidence: scripts/fet_verify_smoke.py."""

from dataclasses import dataclass, field

import pytest

from buyer_gateway.payment_verifier import check_transfer, verify_payment

BUYER, GATEWAY = "fetch1buyer", "fetch1gateway"
HASH = "AB" * 32


@dataclass
class Log:
    events: dict


@dataclass
class Tx:
    code: int = 0
    events: dict = field(default_factory=dict)
    logs: list = field(default_factory=list)
    hash: str = HASH
    height: int = 1


def tx(amount="100000000000000000atestfet", sender=BUYER, recipient=GATEWAY, code=0):
    return Tx(code=code, logs=[Log({"transfer": {"sender": sender, "recipient": recipient, "amount": amount}})])


def check(t, units=10**17):
    return check_transfer(t, sender=BUYER, recipient=GATEWAY, amount_units=units, denom="atestfet")


def test_exact_transfer_verifies():
    v = check(tx())
    assert v.verified and v.proof["paid_units"] == 10**17


@pytest.mark.parametrize("t, why", [
    (tx(code=5), "failed"), (tx(amount="10atestfet"), "below"), (tx(recipient="fetch1other"), "no transfer"),
    (tx(sender="fetch1someoneelse"), "no transfer"), (tx(amount="100000000000000000afet"), "no transfer"),
])
def test_wrong_transfers_never_verify(t, why):
    v = check(t)
    assert not v.verified and why in v.detail


class Ledger:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, 0

    def query_tx(self, h):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


async def test_malformed_hash_mainnet_and_missing_tx():
    base = {"tx_hash": HASH, "sender": BUYER, "recipient": GATEWAY, "amount_units": 10**17, "denom": "atestfet",
            "network": "stable-testnet"}
    assert (await verify_payment({**base, "tx_hash": "nope"}, ledger=Ledger())).detail == "malformed transaction hash"
    assert not (await verify_payment({**base, "network": "mainnet"}, ledger=Ledger())).verified
    missing = Ledger(error=RuntimeError("not found"))
    v = await verify_payment(base, ledger=missing, attempts=2, delay_s=0)
    assert not v.verified and missing.calls == 2
    assert (await verify_payment(base, ledger=Ledger(tx()), delay_s=0)).verified
