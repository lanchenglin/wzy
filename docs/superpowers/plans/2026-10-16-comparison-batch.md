# Comparison batch implementation plan

**Goal:** Deliver advisory matching, all-product multi-store comparison, opt-in readonly scheduling, and a durable in-app inbox.

**Architecture:** Pure deterministic comparison functions consume snapshot and observation evidence. SQLite persists scheduler settings and events. One collection coordinator shares the existing asyncio/account locks with listings; a database-instance flock is acquired before recovery. No external notification transport.

**Tech stack:** Existing Python/FastAPI/SQLite, plain browser JS, pytest/jsdom; no new dependencies.

**Spec:** Authorized brief `/home/lan/.hermes/cache/scratch/wzy-comparison-brief.md`; scope reproduced below to keep this plan portable.

## Constraints

- Synthetic fixtures/temp DB only; no real CDP, production DB, profile, credentials, service changes, commits or pushes.
- Preserve existing matches, audits and complete-snapshot precedence; partial observations are separately visible.
- Suggestions expose family/model/plan/quota/duration/delivery/warranty, unknowns and conflicts; never persist without human confirmation.
- Compare all products and confirmed multi-store groups. Decimal differences/percentages; fresh, complete, explicitly available competitors only qualify for minima. SKU coverage remains unknown.
- Scheduler disabled initially, per-source intervals 15–1440 minutes (60 default), global pause, manual resume after challenge/login, bounded backoff, one bounded task, no catchup storm.
- Persist baseline-based observed price/explicit stock changes, run aggregation and diagnostic transitions; no failed/paused price alerts or missing-product removal; durable dedupe/read/ack.

## Execution checklist

- [x] Comparison: add `tests/test_comparison.py`, run red, implement `src/wzy/comparison.py`, expose evidence from Store, run green. Cases: conflicting known fields and unknowns, PPO, multi-store minima, stale/partial/stock exclusions, Decimal zero denominator.
- [x] Inbox: add `tests/test_notifications.py`, run red, implement atomic finish-run event generation and persistent inbox in `src/wzy/notifications.py`/Store, run green. Cases: first baseline, repeated finish, partial observations, failed/paused diagnostics, restart/read/ack.
- [x] Scheduler: add `tests/test_scheduler.py`, run red, implement `src/wzy/scheduler.py` and Store persistence; integrate app lifespan and shared collection path. Cases: default off, interval bounds, restart rebase, pause/manual/resume, backoff, cancellation, no overlap, exclusive DB lease before recovery.
- [x] UI/API: add API and DOM regressions, run red, integrate comparison/suggestions, schedule controls, inbox actions into existing accessible textContent UI. Validate local boundary and manual confirmation.
- [x] Documentation and verification: update README, AGENTS, portable Skill and handoff with exact configuration/test/acceptance commands, run all Python/DOM/syntax/compile/dependency/diff checks, review changes and report live limits separately.
