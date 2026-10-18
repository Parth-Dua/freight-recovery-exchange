# Freight Exchange: design spec

One document for the design agent and the build agent. Follow it exactly.

## Product
A market where AI agents negotiate ocean freight. Carrier staff use a web console to set
their pricing agent and cancel risky sailings. Buyers act only in the ASI:One chat app and
get a read-only shipment page. Judges watch a read-only market view where agents negotiate
and the exchange rebooks stranded cargo.

## Look
Charcoal, flat, port-signage type, hi-vis yellow for actions, cargo colors for carriers.
Calm everywhere except one big element per screen.
Never: glow, gradients, 3D, isometric, rendered ships, cyan, navy blue backgrounds, pill
chips on everything, a card around every block, emoji, stock photos.

## Tokens
Colors
- bg #131416 · surface #1B1D20 · raised #23262A · line #2F3338
- ink #EDEBE6 · muted #A3A6AA · faint #7D8186 (minimum for any text)
- accent hi-vis yellow #FFD31A (primary actions only), text on it #131416
- ok #43C47A · warn #FFA51F · danger #FF5A4E (always with an icon or a word)
- magenta #E5399B: Hormuz advisory and X-ray only
- carriers: Atlas #F26B1D orange · Beacon #8B6CF0 violet · Cove #1FAE6E green ·
  Meridian #3B6FE8 blue · Dhow #C08A55 teak

Type (Google Fonts)
- Barlow Condensed 600: display 56/60, h1 36/40, h2 24/30
- Barlow: h3 600 18/24 · body 400 16/24 · small 400 14/20 ·
  label 600 12/16 uppercase, +0.08em letter spacing
- IBM Plex Mono 500: IDs, ports, prices, times 14/20 · big numbers 48/52 · tabular figures

Layout
- Spacing 4 8 12 16 24 32 48 64 · radius 2px on controls, 0 on panels
- Separate sections with 1px lines and space, not boxes. Box only the selected item.
- Desktop 1440 wide; the shipment page is mobile 390 wide.

Container component (used on every screen)
- 64×36, carrier color fill, 1px darker stroke, vertical ribs every 6px at 18% black,
  4×4 corner castings in each corner, small ISO ID below in mono (ATLU 410227 3)
- Empty slot: 1px dashed faint outline. Stranded: Atlas orange with a 45° hatch.

Map
- Flat. Land one step lighter than the sea, one thin coastline stroke, faint lat/long
  labels on one edge only. Ships are small flat chevrons.
- Every route from Jebel Ali exits the Persian Gulf through the Strait of Hormuz, around
  the Musandam peninsula, then to its destination.
- Advisory zone: magenta 45° hatch over the Strait of Hormuz, labeled "Hormuz advisory".

## Data (use these, never invent)
- Ports: AEJEA Jebel Ali, INBOM Mumbai, INMUN Mundra, PKKHI Karachi, SADMM Dammam,
  OMSOH Sohar. 20ft containers only.
- Sailings: Atlas ATL-101, ATL-102, ATL-103 · Beacon BEA-201 · Cove COV-301.
- Atlas pricing: target $2,600, floor $2,300 per container. At half full it opens at
  $2,730 and accepts down to $2,539.
- Advisory: SIM-ADV-2026-014, vessel security incident, Strait of Hormuz.
- Times with seconds (10:14:07), uneven prices, booking IDs like BK-0A14FFE2.

Recovery story (ATL-101 withdrawn, 3 bookings, 4 containers)
- Buyer A: 2 containers by 25 Oct → Cove COV-301 (arrives 24 Oct). Confirmed.
- Buyer B: 1 container by 22 Oct → Beacon BEA-201 (arrives 22 Oct, 2 open slots, ship
  half full). Beacon opens $6,000, B counters $5,300, agreed $5,500. B's hidden max $5,800
  ($5,000 original + $800 pre-approved). Beacon's hidden floor $5,100. Confirmed.
- Buyer C: 1 container by 22 Oct → Beacon. Hidden max $5,000 ($0 pre-approved), below
  Beacon's floor, so C must approve the extra cost in ASI:One.
- Cove arrives after 22 Oct, so Cove is never paired with B or C.
- First come, first served would rebook 1 of 3. The exchange rebooks 3 of 3.

## Screens

Carrier console. Top bar: carrier switcher "Atlas Lines" · Sailings · Pricing ·
Agent activity · Risk.

1. Sailings
   Left: list of sailings (ID, route, date, status word, row of containers). Middle: preview
   with a flat side-profile ship whose deck containers are the real slots (3 filled, 1
   empty), route, arrival, "Open sailing". Right: map with the route and advisory.

2. Sailing detail
   Big ID, status, slots as containers, route, departure and arrival, price per container.
   "Edit pricing" (accent) and "Review cancellation" (danger outline). Advisory row linking
   to Risk. Map on the right.

3. Pricing
   Business goal segmented control: Fill the ship / Balanced / Protect margin. Two fields:
   Opening target (USD), Floor (USD) with "Your agent never accepts less." Collapsed
   "Advanced pricing": price rise as the ship fills (%), max discount empty ship (%), max
   discount full ship (%), crisis surge for stranded cargo (%). Right: live quote preview
   on a real sailing (its containers at their fullness, "Opens at", "Accepts down to", lock
   note "Only your agent sees these limits"), Save (accent), Discard, "Saved · version 3".
   States: field error inline, "someone else saved first" banner.

4. Agent activity
   Status "Running" with "Pause agent". Three plain figures: actions today, limit
   breaches, blocked. Table: time, action, sailing, buyer (Buyer A/B/C), price, check
   (✓ or ⚠ with text, for example "⚠ Below floor ($2,300): blocked, not sent"). Clicking a
   row opens a drawer with a guardrail checklist (signed by Atlas's key, sailing active,
   space available, round allowed, price vs floor, discount limit). Paused state: warning
   banner "Agent paused. It declines all new requests until you resume." with "Resume".
   Pausing asks for confirmation on the page.

5. Risk
   Big "Strait of Hormuz", one plain sentence, the affected sailing with bookings and
   containers, "Review affected sailing". Map zoomed on the strait. The review step lists
   the bookings that will be disrupted; the confirm button is danger.

Market view (read-only). Top bar: "Freight exchange" · X-ray toggle · "Demo view · Read
only". Step tracker: 1 Disruption · 2 Negotiation · 3 Allocation.

6. Disruption
   The withdrawn ship's profile; its orange containers lift off and become stranded.
   "ATL-101 withdrawn · 3 bookings · 4 containers".

7. Negotiation
   Left: buyers (their stranded containers) linked by lines to carriers. Line states:
   negotiating, agreed, declined. Dhow shows "No route". Right: the selected session as
   agent messages with times and prices, for example Beacon "We can take 1 container.
   Arrival 22 Oct. $6,000", Buyer B "Comparable offers are around $5,300. Can you meet it?",
   Beacon "We can meet at $5,500." Label "Round 1 · agreed".
   X-ray on: thin magenta banner "Showing private limits the agents never shared. Demo
   only." Under the messages a horizontal price track $5,000 to $6,200: floor $5,100 and
   max $5,800 as locked dashed lines, the deal zone shaded between them, dots for opening
   $6,000 → counter $5,300 → final $5,500 joined in order, "Agreed $5,500 · inside the
   gap", "Beacon left $400 on the table. Buyer B saved $300 below its limit." Buyer C's line
   gets one chip: "outside limits · needs buyer approval". No other X-ray chips.

8. Allocation
   Only winning lines stay; containers move onto the new carriers' ships. Two big blocks:
   "1 / 3 · first come, first served" (neutral gray) and "3 / 3 · exchange" (accent).
   Allocation row: A A B C → Cove (A, A), Beacon (B confirmed, C awaiting approval).

Buyer

9. Shipment (mobile, from the chat link)
   Big header block "Replacement found", route, your container with its ISO ID on the new
   ship, timeline with times (booked, sailing withdrawn, replacement found, awaiting your
   approval or confirmed), cost (original + extra = total, "within your $800 allowance"),
   "Review in ASI:One".

## Copy
Short and operational, like a port operator talking. No taglines, no "seamless", no
"AI-powered", no exclamation marks. Name things by what users recognize.

## Checklist before handing off
- No color is used for two meanings. Carrier colors only mean carriers.
- Every status has an icon or word, not color alone. All text at least 4.5:1 contrast.
- One clearly biggest element per screen.
- Every number and ID matches the data section.
