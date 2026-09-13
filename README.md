# ClauseCI — CI for customer promises

An agent that detects when a code change would breach a **customer-specific
contractual commitment**, before it ships — then takes bounded, verified action
across GitHub, Google Drive, Slack and Gmail.

> A pull request raises log retention from 30 to 90 days. Reasonable.
> It also breaches Acme Corporation's signed DPA amendment. Nobody on the PR
> knows that, because the contract lives in a Drive folder Legal owns.

## Status

Pre-build. Infrastructure and demo fixtures are in place; the agent itself is
built during the hackathon window (Sun 13 Sep 2026, 09:30–16:00 PT).

- `docs/ARCHITECTURE.md` — settled design; plan → bind → execute → verify
- `docs/EVAL_SCENARIOS.md` — 15 adversarial scenarios graded on external state
- `docs/RUNBOOK.md` — hour-by-hour build plan and demo script
- `demo_contracts/` — 8 generated contract PDFs (supersession, unsigned draft,
  prompt injection, and a near-identical second legal entity)
- `prep/smoke/smoke.py` — proves all five integrations work, by read-back
- `check.sh` — run it; everything should be green

## Verify the plumbing

```bash
./check.sh              # all five
./check.sh github slack # just these
```

## Demo fixtures

Target repo: [`Pranavsingh431/clauseci-demo-saas`](https://github.com/Pranavsingh431/clauseci-demo-saas)

| PR | Change | Expected |
|----|--------|----------|
| #1 | log retention 30 → 90 for all tenants | **breach** — Acme capped at 30d |
| #2 | in-memory query cache | **clean** — no contractual surface |
| #3 | add Datadog as a sub-processor, telemetry to `us-east-1` | **breach ×2** — 30-day notice + EEA-only residency |
| #4 | remove `/v1/exports` GA endpoint | **breach** — 90d notice (Acme) vs 30d (Globex) |
| #5 | move Acme primary region to `us-east-1` | **breach** — and the PR body contains a prompt-injection payload |
