"""On-chain proof for a Fetch Payment Protocol commit (FET stable testnet, denom atestfet).

A CommitPayment is only a claim. This queries the Fetch ledger for the transaction and checks it
succeeded and moved at least the expected units from the buyer's wallet to the gateway's wallet.
Pure predicate over an injected ledger client, so it is unit-testable without the network.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

TX_HASH = re.compile(r"^[0-9A-Fa-f]{64}$")


@dataclass(frozen=True)
class LedgerVerdict:
    verified: bool
    detail: str
    proof: dict = field(default_factory=dict)


def _amount_units(amount: str, denom: str) -> int:
    """Sum of `denom` in a Cosmos amount string like '100000000000000000atestfet,5afet'."""
    total = 0
    for part in (amount or "").split(","):
        m = re.fullmatch(r"\s*(\d+)([a-zA-Z/]+)\s*", part)
        if m and m.group(2) == denom:
            total += int(m.group(1))
    return total


def check_transfer(tx, *, sender: str, recipient: str, amount_units: int, denom: str) -> LedgerVerdict:
    if tx.code != 0:
        return LedgerVerdict(False, f"transaction failed on-chain (code {tx.code})")
    transfers = [log.events.get("transfer", {}) for log in (tx.logs or [])] + [tx.events.get("transfer", {})]
    paid = 0
    for t in transfers:
        if t.get("recipient") == recipient and (not t.get("sender") or t.get("sender") == sender):
            paid = max(paid, _amount_units(t.get("amount", ""), denom))
    proof = {"tx_hash": tx.hash, "height": tx.height, "recipient": recipient, "sender": sender,
             "paid_units": paid, "expected_units": amount_units, "denom": denom}
    if paid == 0:
        return LedgerVerdict(False, "no transfer from the buyer wallet to the gateway wallet in that transaction", proof)
    if paid < amount_units:
        return LedgerVerdict(False, "amount below the requested fee", proof)
    return LedgerVerdict(True, "verified", proof)


async def verify_payment(expected: dict, *, ledger=None, attempts: int = 8, delay_s: float = 2.5) -> LedgerVerdict:
    tx_hash = expected["tx_hash"]
    if not TX_HASH.match(tx_hash or ""):
        return LedgerVerdict(False, "malformed transaction hash")
    if expected.get("network") != "stable-testnet":
        return LedgerVerdict(False, "only the Fetch stable testnet is accepted")
    if ledger is None:
        from cosmpy.aerial.client import LedgerClient, NetworkConfig
        ledger = LedgerClient(NetworkConfig.fetchai_stable_testnet())
    last = "transaction not found on the Fetch testnet"
    for attempt in range(attempts):  # a fresh tx can take a few seconds to be indexed
        try:
            tx = await asyncio.to_thread(ledger.query_tx, tx_hash.upper())
        except Exception as exc:
            last = f"transaction not found on the Fetch testnet ({type(exc).__name__})"
            await asyncio.sleep(delay_s)
            continue
        return check_transfer(tx, sender=expected["sender"], recipient=expected["recipient"],
                              amount_units=int(expected["amount_units"]), denom=expected["denom"])
    return LedgerVerdict(False, last)
