"""REQUEST_OFFER / PROPOSE_OFFER / REJECT_OFFER / AGENT_ERROR (schema 1.0).

These are our application messages carried as typed uAgents models — not
built-in ACP verbs. Money is integer minor units + ISO currency; timestamps
are timezone-aware UTC. Fixture values are simulated and labelled as such.

uagents_core ``Model`` is a pydantic **v1** model, so v1 validators are used.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional, Union

from pydantic.v1 import Field, ValidationError, root_validator, validator
from uagents_core.models import Model

SCHEMA_VERSION = "1.0"

ContainerType = Literal["20GP", "40GP", "40HC"]
SourceKind = Literal["simulated", "external"]


def _require_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


class RejectReason(str, Enum):
    NO_ELIGIBLE_SAILING = "NO_ELIGIBLE_SAILING"
    INSUFFICIENT_CAPACITY = "INSUFFICIENT_CAPACITY"
    BUDGET_BELOW_POLICY = "BUDGET_BELOW_POLICY"
    UNSUPPORTED_CORRIDOR = "UNSUPPORTED_CORRIDOR"
    UNSUPPORTED_CONTAINER_TYPE = "UNSUPPORTED_CONTAINER_TYPE"


class ErrorCode(str, Enum):
    # Raised by a carrier
    MALFORMED_REQUEST = "MALFORMED_REQUEST"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
    REQUEST_EXPIRED = "REQUEST_EXPIRED"
    UNAUTHORIZED_REQUESTER = "UNAUTHORIZED_REQUESTER"
    INVALID_CARGO_REQUEST = "INVALID_CARGO_REQUEST"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # Raised by the Exchange about a carrier interaction
    CARRIER_TIMEOUT = "CARRIER_TIMEOUT"
    CARRIER_UNREACHABLE = "CARRIER_UNREACHABLE"
    MALFORMED_REPLY = "MALFORMED_REPLY"
    UNKNOWN_CARRIER = "UNKNOWN_CARRIER"
    INVALID_SIGNATURE = "INVALID_SIGNATURE"
    CORRELATION_MISMATCH = "CORRELATION_MISMATCH"
    STALE_OFFER = "STALE_OFFER"
    OFFER_CONSTRAINT_VIOLATION = "OFFER_CONSTRAINT_VIOLATION"


class CargoSpec(Model):
    origin_port: str = Field(..., min_length=5, max_length=5)
    destination_port: str = Field(..., min_length=5, max_length=5)
    container_type: ContainerType
    quantity: int = Field(..., ge=1, le=50)
    earliest_departure: Optional[datetime] = None
    latest_arrival: Optional[datetime] = None
    max_total_price_minor: Optional[int] = Field(None, ge=1)
    currency: str = Field("USD", min_length=3, max_length=3)
    allow_split: bool = False

    @validator("earliest_departure", "latest_arrival", allow_reuse=True)
    def _utc(cls, value):
        return _require_utc(value)

    @validator("origin_port", "destination_port", "currency", allow_reuse=True)
    def _upper(cls, value: str) -> str:
        return value.upper()

    @root_validator(skip_on_failure=True, allow_reuse=True)
    def _consistent(cls, values):
        if values["origin_port"] == values["destination_port"]:
            raise ValueError("origin and destination must differ")
        dep, arr = values.get("earliest_departure"), values.get("latest_arrival")
        if dep and arr and arr <= dep:
            raise ValueError("latest_arrival must be after earliest_departure")
        return values


class _Envelope(Model):
    schema_version: str = SCHEMA_VERSION
    message_id: str
    correlation_id: str
    request_id: str
    negotiation_id: str
    sent_at: datetime

    @validator("sent_at", allow_reuse=True)
    def _utc_sent(cls, value):
        return _require_utc(value)


class RequestOffer(_Envelope):
    message_type: Literal["REQUEST_OFFER"] = "REQUEST_OFFER"
    expires_at: datetime
    cargo: CargoSpec

    @validator("expires_at", allow_reuse=True)
    def _utc_expires(cls, value):
        return _require_utc(value)


class _SignedReply(_Envelope):
    carrier_id: str
    carrier_address: str
    in_reply_to: str  # message_id of the REQUEST_OFFER
    signature: str = ""  # carrier signature over every other field


class ProposeOffer(_SignedReply):
    message_type: Literal["PROPOSE_OFFER"] = "PROPOSE_OFFER"
    offer_id: str
    sailing_id: str
    vessel_name: str
    origin_port: str
    destination_port: str
    container_type: ContainerType
    quantity: int = Field(..., ge=1)
    total_price_minor: int = Field(..., ge=1)
    currency: str
    departure_at: datetime
    arrival_at: datetime
    expires_at: datetime
    round: int = 1
    terms: str = "Indicative quote; not a capacity hold or booking."
    source_kind: SourceKind = "simulated"

    @validator("departure_at", "arrival_at", "expires_at", allow_reuse=True)
    def _utc_times(cls, value):
        return _require_utc(value)


class RejectOffer(_SignedReply):
    message_type: Literal["REJECT_OFFER"] = "REJECT_OFFER"
    reason_code: RejectReason
    reason: str


class AgentError(_SignedReply):
    message_type: Literal["AGENT_ERROR"] = "AGENT_ERROR"
    error_code: ErrorCode
    detail: str


CarrierReply = Union[ProposeOffer, RejectOffer, AgentError]
_REPLY_TYPES = {m.__fields__["message_type"].default: m for m in (ProposeOffer, RejectOffer, AgentError)}


class MalformedReply(ValueError):
    """Reply payload is not valid JSON or does not match a known reply schema."""


def parse_carrier_reply(raw: Union[str, bytes, dict]) -> CarrierReply:
    """Parse a sync reply payload by its ``message_type``."""
    try:
        data = raw if isinstance(raw, dict) else json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise MalformedReply(f"reply is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MalformedReply("reply is not a JSON object")
    model = _REPLY_TYPES.get(data.get("message_type"))
    if model is None:
        raise MalformedReply(f"unknown message_type {data.get('message_type')!r}")
    try:
        return model.parse_obj(data)
    except ValidationError as exc:
        raise MalformedReply(f"{model.__name__} failed validation: {exc.errors()[:3]}") from exc
