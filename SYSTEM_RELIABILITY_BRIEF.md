# ClauseCI System and Reliability Brief

## What it does

ClauseCI is a customer aware release decision agent. When a pull request changes
product configuration, it resolves the effective value for every customer, reads
the signed agreements that govern those accounts, and decides whether the change
breaches a represented obligation. It publishes that decision as a required
GitHub commit status, opens one Slack engineering case with the controlling
clause quoted, and proposes a correction that keeps the requested behaviour
wherever the agreements permit it. A developer applies the correction. ClauseCI
then verifies the result and resolves the same case. It does not commit, push or
merge code.

## Supported workflow

Three external applications, all live.

| App | Role | Access |
|---|---|---|
| GitHub | the proposed change, and where the decision is recorded | read pull request and diff, write commit status |
| Google Drive | the signed agreements | read only, at the OAuth scope |
| Slack | the engineering case | post, update and read back in one channel |

Supported domain: customer specific log retention, over application logs,
diagnostic logs, and audit logs where a clause actually names them. Gmail is
verified infrastructure and is deliberately not part of the workflow.

## Model boundary

The model interprets heterogeneous contract language into typed obligations. It
returns a number, a unit, the categories a clause covers, and a verbatim quote.
It is given no tools, so it cannot write a status, post a message or call an API.

Deterministic code owns everything else: customer identity, source eligibility,
configuration resolution, predicate evaluation, candidate construction, ranking,
authorization, provider writes, verification and recovery.

Two checks make a quote usable, and neither implies the other. The quote must
occur verbatim in the source, **and** it must actually state the number being
claimed. A real sentence that mentions no number cannot justify one. That second
check was added after a test drove a stubbed model into producing a 9999 day
obligation from a genuine sentence in a document.

## Evidence binding

Every decision is pinned to the exact head SHA, the exact base SHA, a digest of
the whole source corpus, the controlling source id and its content digest, the
quoted clause, and the parser, policy, decision policy, semantic model and
prompt versions. A receipt is valid only for the revision it was sealed on.

The corpus digest is computed from file identity, provider revision and content
digest, sorted by identity. Renaming a document does not change it. Changing a
byte does.

## External effect safety

The claim, stated narrowly: **ClauseCI records intended effects before
execution, reconciles uncertain writes against provider state, and refuses blind
retries when a prior outcome cannot be established.** This is not exactly once
execution. There is no distributed transaction and no atomic commit across two
providers, and neither is claimed.

- **Planned effect journal.** Intent is written to local SQLite and committed
  before any provider is called.
- **Stable case identity.** Derived from namespace, repository and pull request
  number, never the head SHA, so a new commit continues the same case.
- **Effect identity.** Derived from case, analysis, provider, action, target and
  payload digest. Never random.
- **UNKNOWN.** A write that may have landed is never collapsed into FAILED. The
  difference decides whether a retry is safe.
- **Reconciliation.** The stored Slack resource id is the normal lookup. A
  marker search of channel history is the recovery path. One match is adopted,
  zero means absent, more than one is ambiguity and ClauseCI refuses to add a
  third.
- **Provider read back.** After every write, state is read back and compared
  field by field. An unrelated ClauseCI message is not accepted just because it
  exists.
- **Freshness.** Rechecked before the first write, between writes, and again
  before sealing. A move before any write means nothing is written at all.

## Measured evaluation

From `evals/results/latest-summary.json`, generated from raw records.

| Metric | Result |
|---|---|
| **False greens** | **0 of 11 unsafe or unresolved cases** |
| Semantic scenarios | 10 of 10 unique |
| Safe supported completion | 5 of 5 |
| Recovery | 5 of 5 recoverable fault cases |
| Verified live multi app workflows | 2 of 2 |
| Duplicate Slack root cases | 0 |
| Wrong target writes | 0 |
| Unknown outcomes after reconciliation | 0 |
| Engineering regression tests | 455 passing |

The regression suite is engineering evidence. It is separate from the evaluation
score and the two are never added together.
10 unique semantic scenarios produced
20 model dependent executions, because the
high risk ones run three times with the cache disabled.

The evaluation combines **local fault injection** against fake providers with
**real provider evidence** from GitHub, Google Drive and Slack. These are kept
visibly separate. A simulated fault is never described as a provider outage, and
a real provider read is never described as simulated. The live results are read
only confirmation of state that the real runs produced.

One evaluation expectation was wrong and is recorded rather than hidden. A
negative control expected NO_SUPPORTED_CHANGE on a pull request that in fact
carries a pre existing 90 day audit retention against a 30 day cap. The analyzer
returned CONFLICT and was right. The failing record is preserved under
`evals/results/superseded/` with the source configuration quoted.

## Limitations

- One obligation family, RETENTION_UPPER_BOUND, over three log categories.
- A synthetic contract corpus of eight documents and a cohort of three customers.
- The execution lock is local and does not coordinate across machines.
- Slack marker recovery scans a bounded slice of channel history.
- A generic provider exception maps to UNKNOWN, so some definitively failed
  writes read as uncertain. That is the conservative direction.
- ClauseCI checks configuration, not the running system. It does not verify that
  production actually deleted anything.
- Not universal legal compliance. A pass is a scoped release decision over the
  represented supported retention predicates, for one recorded configuration and
  one recorded source snapshot.
