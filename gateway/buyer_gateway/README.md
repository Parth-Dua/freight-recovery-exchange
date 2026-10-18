# Hormuz Exchange — Ocean Freight & Container Shipping Booking Agent

Book container shipping by chat. Tell this agent what cargo you need to ship and where; it gets **freight quotes
from independent ocean carriers**, **negotiates the shipping rate for you**, books the sailing, takes **card
payment**, and **automatically rebooks your cargo if a carrier cancels the sailing** (for example after a Strait of
Hormuz security incident). Built for shippers, importers, exporters and freight forwarders moving 20ft containers
between the Gulf (Dubai / Jebel Ali, Dammam, Sohar) and India / Pakistan (Mumbai, Mundra, Karachi).

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)
![domain:logistics](https://img.shields.io/badge/logistics-freight-0A7E8C)

## Key features

- **Container shipping quotes** — 20ft (20GP) container freight rates from several ocean carriers at once.
- **Automatic freight rate negotiation** — your buyer agent counters each carrier up to twice, in parallel, and
  never reveals your budget.
- **Negotiation transcripts** — see exactly how each carrier's price moved (opening quote → final offer).
- **Book and pay in chat** — the carrier holds your space, you pay by card (Stripe checkout), you get a booking
  confirmation with a claim code.
- **Disruption recovery and automatic rebooking** — if your sailing is cancelled (security incident, port
  congestion, vessel fault), the exchange negotiates replacement space with other carriers and rebooks you within
  the extra cost you pre-approved, or asks you first. If the new sailing costs more, you pay only the fare difference.

## How to use

Just describe your shipment in plain English. The agent replies with a short form, then offers, then a booking.

```text
You:   I need to ship 2 containers from Jebel Ali to Mumbai by next week
Agent: [form] origin, destination, number of 20ft containers, arrive-by date, split allowed?, cargo class,
       priority (cheapest / balanced / fastest), optional max budget, auto-rebook limit
You:   (submit the form)
Agent: 2 of 2 carriers quoted. Your agent is countering now…
Agent: [offers] Atlas Lines ATL-101 · $5,010 · 2×20GP · departs Oct 19 → arrives Oct 21 · Negotiated −6%
       Beacon Maritime BEA-201 · $5,170 · …
You:   (choose an offer) → review → Confirm and pay
Agent: [card payment] → Booking confirmed · BK-… · claim code
```

```text
Agent: ⚠️ SAILING CANCELLATION ALERT — trying to negotiate alternate sailings for BK-… now.
Agent: 🔔 Approve your replacement sailing: Beacon BEA-201, arrives Oct 22 (you pay the fare difference)
You:   Approve
Agent: ✅ REBOOKED — confirmed by the carrier
```

Other things you can say:

- "Get me freight quotes for a 20ft container from Dubai to India"
- "How much does it cost to ship a container from Jebel Ali to Mumbai?"
- "Book ocean freight from Dammam to Mumbai"
- "Find a shipping line with space from the UAE to Karachi"
- "Compare container shipping rates to Mundra"
- "Show me how Atlas negotiated" · "paid" (check a card payment) · "back to offers"

## Use cases

- A shipper or importer needs a container booked on a specific lane by a deadline, at the best negotiated rate.
- A freight forwarder wants several carriers' quotes compared and negotiated without emailing each line.
- Cargo is stranded because a carrier cancelled a sailing (e.g. Hormuz disruption) and must be rebooked fast,
  jointly with other affected shippers, without overpaying.

## Limitations

- Lanes: Jebel Ali (Dubai), Dammam, Sohar → Mumbai, Mundra, Karachi; 20ft containers (20GP), 1–10 per booking.
- Card payments run in Stripe sandbox (test) mode for this deployment.
- Carriers, vessels, schedules and rates are a demonstration market operated by Hormuz Exchange.

## Metadata

- Built by Hormuz Exchange (LA Hacks 2026) on Fetch.ai uAgents: Chat Protocol, Interactive Cards and the Agent
  Payment Protocol; carrier agents are independent uAgents; data on Tiger (TimescaleDB).
- Keywords: freight, shipping, container shipping, ocean freight, freight quote, shipping rates, cargo booking,
  logistics, freight forwarding, carrier negotiation, rebooking, supply chain disruption, Strait of Hormuz,
  Dubai, Jebel Ali, Mumbai, India, Gulf, Middle East.
