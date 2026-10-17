"""Agentverse profile content: truthful about Phase 1 being discovery/capabilities only.

Discoverability keywords are not part of the SDK's AgentProfile; set them in the
Agentverse UI: ocean freight, cargo booking, shipping agent, shipment recovery,
maritime disruption.
"""

from uagents_core.registration import AgentProfile

DESCRIPTION = (
    "Demo ocean-freight exchange: asks independent carrier agents for signed quotes in parallel and "
    "records them in Tiger. Fictional carriers, Jebel Ali→Mumbai. Booking/payment not active yet."
)

README = """# Freight Recovery Exchange (Demo)
Agent-native ocean-freight exchange, designed to help cargo owners negotiate bookings and recover shipments after disruptions.

## What works now
- **Live quotes:** I send your request to independent carrier agents (Atlas, Beacon, Cove) in parallel. Each carrier prices from its own private policy and inventory and signs its offer; I verify every signature before showing it.
- **Auditable:** every request, offer, decline and error is recorded in Tiger (TimescaleDB) with one correlation ID; the `Ref` at the end of each reply is the request ID.
- **Corridor:** Jebel Ali (AEJEA) → Mumbai (INBOM), 20GP containers.

**Demo market:** carriers, sailings and prices are fictional simulations. Quotes are indicative — nothing is held, booked or charged.
**Not yet active:** negotiation, booking, payment, disruption recovery.

## Example prompts
- "Quote 2x20GP from Jebel Ali to Mumbai, arrive by Oct 23, budget $5,600, don't split"
- "What corridors do you support?"
- "What can you do?"
"""

STARTER_PROMPTS = [
    "Quote 2x20GP from Jebel Ali to Mumbai, arrive by Oct 23, budget $5,600, don't split",
    "What corridors do you support?",
]


def build_profile() -> AgentProfile:
    return AgentProfile(description=DESCRIPTION, readme=README, starter_prompts=STARTER_PROMPTS)
