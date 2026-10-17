"""Agentverse profile content: truthful about Phase 1 being discovery/capabilities only.

Discoverability keywords are not part of the SDK's AgentProfile; set them in the
Agentverse UI: ocean freight, cargo booking, shipping agent, shipment recovery,
maritime disruption.
"""

from uagents_core.registration import AgentProfile

DESCRIPTION = (
    "Demo ocean-freight coordination agent (Phase 1: connectivity only). Shows a sample "
    "Shanghai→Los Angeles corridor; booking, payments and recovery are not active yet."
)

README = """# Freight Recovery Exchange (Demo)
Agent-native ocean-freight coordination, designed to help cargo owners negotiate bookings and recover shipments after disruptions.

## Current demo capabilities
- Explain the product workflow.
- Return the Phase 1 sample corridor: Shanghai (CNSHA) to Los Angeles (USLAX).
- Confirm connectivity between ASI:One, Agentverse, and our live LangGraph backend.

**Not yet active:** live quotations, bookings, payments, or recovery.

## Example prompts
- "What can your freight agent do?"
- "What corridors do you currently support?"
"""

STARTER_PROMPTS = [
    "What can your freight agent do?",
    "What corridors do you currently support?",
]


def build_profile() -> AgentProfile:
    return AgentProfile(description=DESCRIPTION, readme=README, starter_prompts=STARTER_PROMPTS)
