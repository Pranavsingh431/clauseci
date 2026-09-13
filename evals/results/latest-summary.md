# ClauseCI evaluation summary

Generated from raw evaluation records. No number here was typed by hand.

- run id: `eval-20260913T175720Z-1a4c5f`
- runtime commit: `e980fed48200737fcb08d2bf9c95cf50d01bcabd`
- generated: 2026-09-13T18:00:40.415178+00:00
- model: `anthropic/claude-sonnet-4.5`, prompt `retention-extract-1.0.0`

This is evaluation evidence. It is separate from the pytest regression suite, which is engineering evidence and is reported separately.

## Headline

- **False greens: 0 of 11 unsafe or unresolved cases.** A false green is a release critical failure.
- Safe case completion: 5 of 5. This exists so that blocking everything cannot look reliable.
- Recovery: 5 of 5 recoverable fault cases (K03, K04, K06, K09, K18).
- Verified live task completion: 2 of 2.

## By mode

| Mode | Result | Kind of evidence |
|---|---|---|
| SEMANTIC | 10 of 10 unique scenarios | real model calls, local contract fixtures, cache disabled |
| DECISION | 20 of 20 | deterministic, no model |
| KERNEL | 20 of 20 | local fault injection, fake providers |
| LIFECYCLE | 5 of 5 | local, fake providers |
| LIVE | 5 of 5 | real GitHub, Google Drive and Slack, read only |

Semantic repeats are counted separately from unique scenarios. 10 unique scenarios produced 20 model dependent executions, of which 10 were repeats and 10 passed.

## Unintended effects

| Effect | Count |
|---|---|
| duplicate slack root cases | 0 |
| wrong target writes | 0 |
| unexpected github contexts | 0 |
| gmail actions | 0 |
| drive mutations | 0 |
| autonomous merges | 0 |
| autonomous remediation commits | 0 |
| unknown outcomes after reconciliation | 0 |

## Cost and latency

Sampled from 4 model dependent evaluation passes. Total reported cost $0.102792, 26552 tokens. Pass latency ranged from 3526.7 ms to 36771.8 ms. The sample is too small for percentiles and none are claimed.

## Failures

No scenario failed in this run.

## What this does not measure

- Only one obligation family, RETENTION_UPPER_BOUND, over three log categories.
- One synthetic contract corpus and one customer cohort.
- Kernel and lifecycle results come from local fault injection, not from real provider outages.
- Live results are read only confirmation of state produced by the real runs.