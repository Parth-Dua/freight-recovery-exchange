# Dashboard design brief (for the Paper design agent)

Paste everything below the line into the design agent.

---

Design a style guide and five screens for **Freight Recovery Exchange**, a market where AI agents
negotiate ocean freight for shippers and shipping companies. When a sailing is cancelled, the
exchange rebooks every stranded container at once. The screens are web dashboards for people who
watch and steer those agents. Buyers never use these screens to act; they act in a chat app. This
is a working first draft for a human designer to refine, so favor clear structure over decoration.

## Who uses it

- **Carrier operations staff** at a shipping company (Atlas Lines, Beacon Maritime, Cove Shipping,
  Meridian Container Line, Dhow Feeder Lines; all fictional). They set how their pricing agent
  behaves and cancel sailings when a risk advisory makes a route unsafe. Busy, numerate, used to
  dense operational tools. Desktop first.
- **Hackathon judges and shippers** watching the market view and a single shipment page. They
  should understand what happened in five seconds.

## Design direction

- Subject matter: container shipping operations. Draw from its real artifacts: bills of lading,
  vessel schedules, port codes (AEJEA, INBOM), container markings, sailing IDs (ATL-101), harbor
  charts. Use that vocabulary in labels and data, not as decorative imagery.
- Tone: calm, precise, trustworthy. Someone is deciding where real cargo goes.
- Dense but legible: tables and numbers line up (tabular figures), clear hierarchy, summary before
  detail.
- Status must be visible at a glance: ACTIVE, WITHDRAWN, DISRUPTED, REBOOKED, NEEDS APPROVAL,
  NOT RECOVERED. Use shape or a small marker plus color, never color alone.
- One accent color. Semantic colors (good, warning, critical) are separate from the accent.
- Light and dark themes, both designed, not inverted.
- Avoid: purple or blue gradients, warm cream with a serif and terracotta, near-black with one
  neon accent, emoji, Inter or Space Grotesk as the main face, every block in an identical rounded
  card, centered layouts, stock ship photos, glowing "AI" motifs.

## Style guide (deliver first)

Colors as named tokens (light and dark), a type scale with a display face, a body face and a mono
face for codes and numbers, spacing scale, radius, table style, buttons (primary, secondary,
destructive), form fields (number with unit, select, checkbox, text), status markers, and an
empty state and error state pattern.

## Screen 1: Carrier, map and analytics

- Header: carrier name, switcher between the five carriers, nav to the three carrier screens.
- Lane map: a schematic (not a satellite map) of 6 ports: AEJEA Jebel Ali, SADMM Dammam,
  OMSOH Sohar, INBOM Mumbai, INMUN Mundra, PKKHI Karachi. Lanes drawn between them, this
  carrier's sailings as markers along lanes, a risk zone overlay ("GULF-TRANSIT, high").
- Sailings table: sailing ID, route, departs, arrives, status, slots sold of total, committed
  bookings. Example: ATL-101, AEJEA to INBOM, departs 18 Oct 12:00, arrives 20 Oct 08:00,
  ACTIVE, 3 of 4 sold, 2 bookings.
- Two charts over the last 24 hours, hourly: utilization percent per sailing, and average price
  per container (USD 2,300 to 2,900 range).
- Small tiles only if they earn it: bookings won vs lost today, revenue today.

## Screen 2: Carrier, preferences (the pricing agent's settings)

- Business goal preset at the top: Fill the ship, Balanced, Protect margin. Picking one fills the
  fields below; the user can then adjust.
- Fields (with units and one-line help text each):
  - Target price per container (USD), e.g. 2,600
  - Floor price per container (USD), e.g. 2,300. Private: the agent never goes below or reveals it.
  - Price rise as the ship fills (%), e.g. 10
  - Max discount on a counteroffer when the ship is empty (%), e.g. 12
  - Max discount when the ship is nearly full (%), e.g. 2
  - Crisis markup when bidding for stranded cargo (%), e.g. 15
  - Quote valid for (minutes), e.g. 10
  - Hold space while a buyer pays (minutes), e.g. 60
  - Offer first: earliest arrival or earliest departure
  - Payment terms (text), e.g. "Freight prepaid to Atlas before departure"
- A live preview panel: "With these settings, a 4-slot ship that is half full opens at $2,730
  and accepts counters down to $2,540."
- Save bar: version number ("Version 3, saved 2 min ago"), Save, Discard. Validation errors
  inline next to the field ("Floor must be at or below the target price").
- A private-data note: these numbers stay with this carrier's agent; buyers and the exchange
  never see them.

## Screen 3: Carrier, risk and cancellations

- Advisory feed: severity, category, region, source reference, time, description, affected
  lanes. Example: HIGH, vessel security incident, GULF-TRANSIT, SIM-ADV-2026-014, "Simulated
  security incident reported in the transit corridor."
- For each advisory, the carrier's sailings it touches, with committed bookings counts.
- Cancel flow: select a sailing, a review step listing the bookings that will be disrupted, a
  destructive confirm button, and a success state ("ATL-101 withdrawn. 2 bookings disrupted.
  Recovery started.").
- Cancellation history log.

## Screen 4: Market view (read-only, for judges)

- Board: stranded bookings after a cancellation (booking ID, containers, deadline) and which
  carriers were paired with each.
- Negotiation sessions: one lane per buyer and carrier pair showing rounds as steps, for
  example "Beacon opens $6,000, buyer counters $5,300, Beacon meets at $5,500, accepted", with a
  one-line note of what the move reveals ("ship half empty").
- Clearing result: who went where, next to what first come, first served would have done
  ("Rebooked 3 of 3" vs "1 of 3").
- A live feel: newest events appear at the top with timestamps.

## Screen 5: Buyer shipment page (read-only, opened from a chat link)

- One booking: booking ID (BK-0A14FFE2), route, sailing, carrier, containers, price paid.
- Status timeline: booked, paid, sailing cancelled, rebooked on BEA-201, arrives 22 Oct.
- If recovered: what changed and why, extra cost, approval status.
- Mobile first for this one screen, since it opens from a phone chat.

## Deliverables

Style guide first, then screens 2, 1, 3, 4, 5 in that order, each in light and dark, desktop width
(1440) plus a 390 width version of screen 5.
