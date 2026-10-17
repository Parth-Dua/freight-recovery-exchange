# 0003 — Demo corridor: Jebel Ali (AEJEA) → Mumbai (INBOM), 20GP

- **Date:** 2026-10-17 · **Status:** Accepted · **Phase:** 2
- **Context:** Phase 1 used a static CNSHA → USLAX sample. PRD §16 fixture (Atlas/Beacon/Cove, Strait of Hormuz disruption) is Gulf → Mumbai, which the Phase 5–7 disruption/recovery story requires.
- **Decision:** Seed fictional carriers on AEJEA → INBOM (route region `STRAIT_OF_HORMUZ`), 20GP only. The Exchange's corridor answer is now read from Tiger (seeded `source_kind='simulated'` sailings) instead of the Phase 1 static catalog. Sailing dates are offsets from a fixture anchor date so the fixture can be re-anchored without code changes.
- **Consequences:** CNSHA → USLAX is no longer offered. All carrier names, vessels, schedules and prices are fictional simulation fixtures.
