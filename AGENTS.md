₹# Coding Agent Rules — Autonomous Freight Recovery

At **every session start**, read `progress.md`, the **active** `docs/specs/NN-*.md`, then relevant parts of `docs/PRD.md`. The PRD is context, **not** permission to implement future phases. Follow explicit recent user decisions over outdated PRD proposals.

1. **One phase at a time.** Do not enter the next phase until active acceptance gates have verified evidence. Part 2 is strictly deferred.
2. **Specs before code.** Implement the smallest tested work package. Verify actual SDK APIs against official docs; do not invent ACP / Stripe / Fetch capabilities.
3. **Mandatory progress log:** Update `progress.md` after each work package, test run, blocker, decision change, and **immediately before stopping**. Keep it short: phase/status, completed, exact test evidence, blockers, changed files, next precise action/command. Do not erase history needed for recovery.
4. **Ownership and git:** Read `OWNERSHIP.md` at session start. Only edit folders owned by the person you are working with; never commit or push to `main`, use a `<name>/<feature>` branch and a PR, and do not merge unless that person explicitly says to.
5. **Safety:** `.env` never committed; sanitize logs; only Stripe `sk_test_` credentials; no real money; no refunds or escrow; deterministic authorization and atomic inventory checks.
6. **Handoff:** Any agent should be able to resume by reading `AGENTS.md`, `progress.md`, active spec and relevant PRD sections only.

**Locked flow:** immediate one-buyer ACP negotiation at booking (max one counter); carrier-authorized sailing withdrawal; one-shot ACP recovery offers; OR-Tools joint allocation; carrier holds/confirmation; ASI:One results. Tiger transactional truth + market event hypertable. Live advisories, Sentry, Market Command Center = Part 2.
