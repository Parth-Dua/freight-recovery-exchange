"""Carrier signatures over reply payloads.

uAgents sync replies come back in an unsigned envelope, so carriers sign the
reply body itself. The Exchange verifies the signature against the carrier
address registered in Tiger — proving which company produced each offer.
"""

from __future__ import annotations

import hashlib
import json

from uagents_core.identity import Identity
from uagents_core.models import Model


def payload_digest(model: Model) -> bytes:
    body = json.loads(model.json(exclude={"signature"}))
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).digest()


def sign_model(model: Model, identity: Identity) -> str:
    return identity.sign_digest(payload_digest(model))


def verify_model(model: Model, address: str) -> bool:
    signature = getattr(model, "signature", "")
    if not signature:
        return False
    try:
        return Identity.verify_digest(address, payload_digest(model), signature)
    except Exception:
        return False
