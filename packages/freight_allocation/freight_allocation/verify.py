"""Step 4 of recovery: check each carrier's claim before trusting it.

Parth's side has already checked the *message* (signature, schema, freshness). This
checks the *claim*: does that ship really have the slots the carrier is offering?

The physical truth is the number of sellable slots Tiger records for each sailing,
net of existing holds and commitments. An offer is trusted for at most that many.
Two offers on the same sailing share one physical limit, so the model also caps
each sailing as a whole (see `model.py`).

Pure: no database here. `capacity.py` supplies the sellable counts.
"""

from __future__ import annotations

from dataclasses import dataclass

from freight_seams import RecoveryOffer


@dataclass(frozen=True)
class ClaimCheck:
    offer_id: str
    sailing_id: str
    carrier_id: str
    claimed: int
    sellable_on_sailing: int

    @property
    def trusted(self) -> int:
        """Units this offer may be used for on its own."""
        return max(0, min(self.claimed, self.sellable_on_sailing))

    @property
    def overstated(self) -> bool:
        return self.claimed > self.sellable_on_sailing


def check_claims(offers: list[RecoveryOffer], sellable: dict[str, int]) -> dict[str, ClaimCheck]:
    """One ClaimCheck per offer. A sailing missing from `sellable` counts as 0:
    if Tiger knows nothing about the sailing, nothing on it is trusted."""
    return {
        offer.offer_id: ClaimCheck(
            offer_id=offer.offer_id,
            sailing_id=offer.sailing_id,
            carrier_id=offer.carrier_id,
            claimed=offer.capacity_units,
            sellable_on_sailing=sellable.get(offer.sailing_id, 0),
        )
        for offer in offers
    }
