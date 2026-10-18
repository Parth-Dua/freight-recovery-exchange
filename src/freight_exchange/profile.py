"""Agentverse profile content: truthful about what the Part 1 demo does.

Discoverability keywords are not part of the SDK's AgentProfile; set them in the
Agentverse UI: ocean freight, cargo booking, shipping agent, shipment recovery,
maritime disruption.
"""

from uagents_core.registration import AgentProfile

DESCRIPTION = (
    "Demo ocean-freight exchange: signed quotes from independent carrier agents, one bounded counteroffer, "
    "booking with Stripe TEST checkout, and joint OR-Tools rebooking when a carrier withdraws a sailing. "
    "Fictional carriers; no real money."
)

README = """# Freight Recovery Exchange (Demo)
Agent-native ocean-freight exchange, designed to help cargo owners negotiate bookings and recover shipments after disruptions.

## What works now
- **Live quotes:** I send your request to independent carrier agents (Atlas, Beacon, Cove, Meridian, Dhow) in parallel. Each carrier prices from its own private policy and inventory and signs its offer; I verify every signature before showing it.
- **Negotiation:** one counteroffer per carrier, never below the carrier's private floor.
- **Booking:** the carrier holds the space, you pay a Stripe TEST checkout (no real money), the carrier confirms, you get a booking ID and claim code.
- **Recovery:** if a carrier withdraws a sailing, I ask the other carriers once for space, decide every affected booking together (OR-Tools), hold and confirm the replacements, and tell you. Anything above the extra cost you approved waits for your "approve BK-…".
- **Auditable:** every request, offer, decline and error is recorded in Tiger (TimescaleDB) with one correlation ID; the `Ref` at the end of each reply is the request ID.
- **Corridors:** Gulf → India/Pakistan (e.g. Jebel Ali AEJEA → Mumbai INBOM), 20GP containers.

**Demo market:** carriers, sailings, prices and sailing withdrawals are fictional simulations. Payments are Stripe TEST mode only.

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
