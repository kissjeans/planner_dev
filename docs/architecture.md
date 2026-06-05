# Architecture Decisions & Deferred Items

## Consciously Deferred (spec §14)

### OR-Tools / PyJobShop (v2 solver)

**Why deferred:** The greedy NetworkX solver handles 50 tasks × 6 people × 120 days in ~10 ms.
Optimal scheduling requires integer programming, adding significant complexity and a heavy dependency.
`SolverPort` is already an interface — swap in OR-Tools without touching callers.
**When to revisit:** When a project produces demonstrably suboptimal plans that cause real missed deadlines.

### Complexity axis (task duration multiplier)

**Why deferred:** ТЗ §18 mentions it; no concrete formula agreed with the client yet.
**When to revisit:** After acceptance run reveals projects consistently running longer than estimated.

### Full task status lifecycle

**Why deferred:** Current MVP tracks `proposed` → `committed`. In-progress / done transitions
need a mobile UX and notifications that are out of scope for single-tenant v1.
**When to revisit:** When the team asks to close tasks from Telegram.

### Hourly scheduling (vs daily capacity ceiling)

**Why deferred:** Daily cap (`capacity_h = 8`) is accurate enough; hourly precision adds schema
complexity without improving the team's planning quality at this stage.
**When to revisit:** When the team has back-to-back external meetings that block half-days.

### Sentry / Prometheus observability

**Why deferred:** Single-process, local deploy. `structlog` JSON + correlation IDs cover triage needs.
**When to revisit:** On first production deploy outside the dev machine.

### Production deploy (Railway / VPS / K8s)

**Why deferred:** KISS — Docker Compose on the developer's machine is the stated deployment target.
**When to revisit:** When the team needs the bot running 24/7 without a laptop open.

### Multi-tenancy

**Why deferred:** One team, one Telegram chat, single-tenant. YAGNI maximum.
**When to revisit:** When a second team requests onboarding.

### E2E tests via real Telegram Bot API on CI

**Why deferred:** Real bot API tests require secrets in CI and a live Telegram chat.
Mock-update tests in `tests/e2e/` cover all flows; real acceptance runs are manual (docs/acceptance.md).
**When to revisit:** On a production pipeline where regressions in Telegram interaction matter.

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Single process (bot + web + scheduler) | One DB writer, no distributed coordination, KISS. |
| `SolverPort` interface | Greedy today, OR-Tools tomorrow — callers unchanged. |
| `BasicIntentParser` fallback | Bot functional with zero API spend; Claude Haiku for quality. |
| `SnapshotCalendar` offline fallback | isdayoff.ru outage doesn't block scheduling. |
| Advisory lock per project_id | Prevents double-booking from concurrent bot + web writes. |
