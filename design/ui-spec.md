# UI spec (compact, for design and build agents)

Freight exchange where AI agents negotiate ocean freight. Carriers use a web console; buyers
act only in ASI:One chat and get a read-only shipment page; judges watch a read-only market view.
Look: charcoal, flat, port-signage type, hi-vis yellow actions, cargo colors. No glow, no
gradients, no 3D, no cyan, no navy, no pill chips everywhere, no card around every block.

## Tokens

Colors
- bg #131416 · surface #1B1D20 · raised #23262A · line #2F3338
- ink #EDEBE6 · muted #A3A6AA · faint #7D8186 (minimum for any text)
- accent (hi-vis yellow, primary actions only) #FFD31A, text on it #131416
- ok #43C47A · warn #FFA51F · danger #FF5A4E (always with an icon or word)
- hazard / X-ray magenta #E5399B (Hormuz advisory and X-ray only)
- carriers: Atlas #F26B1D orange · Beacon #8B6CF0 violet · Cove #1FAE6E green ·
  Meridian #3B6FE8 blue · Dhow #C08A55 teak

Type (Google Fonts)
- Barlow Condensed 600: display 56/60, h1 36/40, h2 24/30
- Barlow: h3 600 18/24, body 400 16/24, small 400 14/20,
  label 600 12/16 uppercase +0.08em
- IBM Plex Mono 500: IDs, ports, prices, times 14/20; big numbers 48/52; tabular figures

Space 4 8 12 16 24 32 48 64 · radius 2px on controls, 0 on panels · 1px lines between
sections instead of boxes · one box only for the selected item

Container component (used everywhere)
- 64×36, carrier color fill, 1px darker stroke, vertical ribs every 6px at 18% black,
  4×4 corner castings in each corner, small ISO ID below in mono (ATLU 410227 3)
- empty slot: 1px dashed faint, faint ribs · stranded: Atlas orange with a 45° hatch

Data: timestamps with seconds, uneven prices, real IDs. Ports AEJEA Jebel Ali, INBOM Mumbai,
INMUN Mundra, PKKHI Karachi, SADMM Dammam, OMSOH Sohar. Atlas sailings ATL-101/102/103,
Beacon BEA-201, Cove COV-301. All routes from Jebel Ali exit through the Strait of Hormuz.
20ft containers only. Atlas pricing: target $2,600, floor $2,300 per container.

## Screens

Carrier console. Top bar: carrier switcher (Atlas Lines) · Sailings · Pricing · Agent activity · Risk

1. Sailings. Left: list of sailings (ID, route, date, status word, container row). Middle:
   preview with a flat side-profile ship whose deck containers are the real slots (3 filled,
   1 empty), route and arrival, "Open sailing". Right: flat dark map, one coastline stroke,
   route through Hormuz, magenta hatched advisory zone.
2. Sailing detail. Big ID, status, slots as containers, route, departure and arrival, price
   per container, "Edit pricing" (accent) and "Review cancellation" (danger outline),
   advisory row linking to Risk. Map on the right.
3. Pricing. Business goal segmented control (Fill the ship / Balanced / Protect margin), two
   fields (opening target, floor), collapsed "Advanced pricing" (rise as ship fills,
   discount empty and full, crisis surge). Right: live quote preview on a real sailing
   (containers at its fullness, "Opens at", "Accepts down to", lock note "Only your agent
   sees these limits"), Save (accent), Discard, "Saved · version 3".
4. Agent activity. Status "Running" + "Pause agent". Three figures: actions today, breaches,
   blocked. Table: time, action, sailing, buyer, price, check (✓ or ⚠ with text). Row drawer
   with guardrail checklist. Paused banner state.
5. Risk. Big "Strait of Hormuz", one line of plain text, affected sailing with bookings and
   containers, "Review affected sailing". Map zoomed on the strait with magenta hatch.
   Review step lists affected bookings; confirm button in danger.

Market view (read-only, "Freight exchange · Demo view"). Step tracker: Disruption ·
Negotiation · Allocation.

6. Disruption. The withdrawn ship's profile; its containers lift off and become stranded.
   "ATL-101 withdrawn · 3 bookings · 4 containers".
7. Negotiation. Left: buyers (stranded containers) linked by lines to carriers; a line exists
   only if that carrier can make that buyer's deadline. Line states: negotiating, agreed,
   declined. Right: selected session as agent messages with times and prices. X-ray toggle:
   thin magenta banner "Showing private limits the agents never shared. Demo only." and a
   horizontal price track under the messages (floor and max as locked dashed lines, shaded
   deal zone, dots opening → counter → final). Buyer C's line chip: "outside limits · needs
   buyer approval".
8. Allocation. Only winning lines stay; containers move onto the new carriers' ships.
   Two big blocks: "1 / 3 · first come, first served" (neutral) and "3 / 3 · exchange"
   (accent). Allocation row: A A B C → Cove (A A), Beacon (B, C awaiting approval).

Buyer

9. Shipment (mobile 390, opened from the chat link). Big block header "Replacement found",
   route, your container with its ISO ID on the new ship, timeline (booked, sailing
   withdrawn, replacement found, awaiting your approval / confirmed) with times, cost
   (original + extra = total, "within your $800 allowance"), "Review in ASI:One".

## Story numbers (keep consistent)

Buyer A: 2 containers by 25 Oct → Cove (arrives 24 Oct). Buyer B: 1 by 22 Oct → Beacon
(arrives 22 Oct), Beacon opens $6,000, B counters $5,300, agreed $5,500; B's max $5,800
($5,000 original + $800 pre-approved), Beacon's floor $5,100. Buyer C: 1 by 22 Oct →
Beacon, max $5,000 ($0 pre-approved), needs approval.
