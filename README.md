# ClauseCI

A customer aware release decision agent.

A pull request raises log retention from 30 days to 90 days. Two lines, in one
`defaults` block. It looks harmless, and for two of three tenants it is. For the
third it breaches a signed data processing amendment that lives in a Google
Drive folder nobody on the pull request has open.

ClauseCI resolves the effective retention value for every customer, reads the
controlling signed agreement, and records a decision on the pull request.

## Status

Phase 4 complete. ClauseCI reads a pull request and its contract evidence, pins
it into an immutable snapshot, extracts source bound retention obligations from
the signed agreements, and produces a deterministic scoped release decision with
three bounded correction candidates.

On the live hero change it reports CONFLICT, because raising two default values
puts Acme Corporation at 90 days against a 30 day cap in its executed amendment.
Globex sits exactly at its 90 day cap and passes. Acme Labs is well under 180.
Acme Labs audit retention has no represented obligation at all, which is reported
as such rather than as a pass.

It then proposes keeping the change for the two customers whose agreements allow
it and pinning only Acme back, preserving 4 of the 6 requested outcomes. That
correction is a proposal. It is not applied, and it does not turn the current
head green.

It then publishes that decision. The failing commit status lands on the exact
analyzed commit under `ClauseCI / retention-compliance`, which is a required
check on the demo repository's protected main branch, so the pull request cannot
be merged through the normal flow while it fails. One Slack engineering case
carries the customer, the clause, the quote and the proposed correction.

Both providers are then read back and checked field by field. The run is only
recorded as verified when both match. Google Drive stays read only and Gmail is
not used.

Every intended effect is written to a local journal before the provider is
called. If a write lands but the response is lost, the effect is recorded as
UNKNOWN rather than guessed at, and the next run reconciles it against provider
state instead of posting again. A restart finds the unfinished effect, adopts the
real Slack case, and leaves exactly one.

This is not exactly once, and does not claim to be. It is: intent recorded before
action, uncertainty kept as uncertainty, and no blind retry when a prior outcome
cannot be established.

When a developer applies the correction and pushes, the new commit is analysed
on its own. It gets its own snapshot, its own analysis and its own status. The
unsafe commit keeps its failing check permanently. The same Slack case, the same
resource, moves to RESOLVED and names both commits.

ClauseCI proposes the correction. A developer applies it. ClauseCI does not
commit, push or merge code, and a test greps the runtime to keep it that way.

## Measured results

From `evals/results/latest-summary.json`, generated from raw records. Run it
with `./.venv/bin/python -m evals.run --mode live`.

**0 false greens across 11 unsafe or unresolved cases.**
A false green is a case that should have blocked a release and did not.

| Mode | Result | Evidence |
|---|---|---|
| SEMANTIC | 10 of 10 unique scenarios | real model calls, cache disabled |
| DECISION | 20 of 20 | deterministic |
| KERNEL | 20 of 20 | local fault injection |
| LIFECYCLE | 5 of 5 | local |
| LIVE | 5 of 5 | real GitHub, Drive and Slack, read only |

Safe case completion 5 of 5,
so blocking everything cannot look reliable. Recovery
5 of 5 recoverable fault cases.
Zero duplicate Slack cases, zero wrong target writes, zero Gmail actions, zero
Drive mutations, zero autonomous merges.

10 unique semantic scenarios produced
20 model dependent executions,
because the high risk ones are repeated three times with the cache disabled.
Those repeats are reported separately and are not counted as extra scenarios.

Separately, the repository has 354 pytest regression tests. That is engineering
evidence, not an evaluation score, and the two are never merged into one number.

See `docs/EVAL_SCENARIOS.md` for the full breakdown and for one case where the
evaluation expectation was wrong and the analyzer was right.

See `BUILD_START_REPORT.md` for exactly what existed before the build window
opened, and what is being built during it.

## Supported scope

Customer specific log retention. `application_logs_days` and
`diagnostic_logs_days` always, `audit_logs_days` only where the controlling
clause explicitly names audit logs.

External apps: GitHub, Google Drive, Slack. Gmail is verified infrastructure but
is not part of this workflow.

Full design in `docs/ARCHITECTURE.md`.

## The three tenants

| Customer | Legal entity | Controlling document | application | diagnostic | audit |
|---|---|---|---|---|---|
| `acme-corp` | Acme Corporation | Amendment No. 1, executed 20 Aug 2026 | 30 | 30 | 30 |
| `globex` | Globex International Ltd. | DPA, executed 2 Mar 2026 | 90 | 90 | 365 |
| `acme-labs` | Acme Labs Pvt Ltd | MSA, executed 11 Jun 2026 | 180 | 180 | not covered |

`acme-corp` and `acme-labs` are separate legal entities with similar names. The
contract for Acme Labs says so explicitly, and the two share no documents.

## What makes the corpus hard

The 8 PDFs in `demo_contracts/` cannot be solved by searching for the word
retention.

| Document | Trap |
|---|---|
| `02_Acme_DPA_2025.pdf` | executed, says 180 days, superseded |
| `03_Acme_DPA_Amendment_2026_SIGNED.pdf` | executed, says 30 days, controls |
| `06_Acme_DPA_Amendment_2026_UNSIGNED_DRAFT.pdf` | newest date, says 365 days, never signed |
| `07_Acme_Security_Addendum_2026.pdf` | executed, contains an instruction telling automated review agents to approve everything |
| `08_AcmeLabs_MSA_2026.pdf` | similar name, different legal entity, 180 days |

## Repository layout

```
clauseci/                    runtime package
  adapters/github.py         read only pull request and file reads
  adapters/drive.py          read only contract document reads
  adapters/openrouter.py     the one semantic component, no tools
  analyzer.py                obligation analysis entry point
  obligations.py             command line entry point
  run.py                     the product command, read only by default
  workflow.py                execution sequencing and verification
  decide.py                  scoped release decision and its entry point
  adapters/github_write.py   commit status writes, nothing else
  adapters/slack_write.py    one engineering case, nothing else
  domain/execution.py        plan, action policy, receipt
  domain/case_message.py     the Slack case and the fields verification checks
  journal.py                 durable SQLite journal of cases, analyses and effects
  reconcile.py               resolving uncertain effects against provider state
  domain/effects.py          effect keys and the effect state machine
  state.py                   lifecycle inspection, reconciliation, evidence
  correction.py              the developer correction artifact
  faults.py                  deterministic fault injection, test use only
  domain/decision.py         decision states, dispositions, the predicate
  domain/candidates.py       three correction constructors and ranking
  domain/rendering.py        patch rendering, verified by re-parsing
  sources.py                 trusted source manifest and derived eligibility
  domain/obligations.py      typed obligation models
  domain/extraction.py       candidate validation and quote binding
  domain/authority.py        eligibility and per category resolution
  domain/prompts.py          versioned prompts and strict schemas
  domain/models.py           typed snapshot models
  domain/snapshot.py         snapshot builder
  domain/classification.py   changed file surface classification
  domain/digests.py          canonical hashing and corpus digest
  config_resolution.py       deterministic effective value resolution
  registry.py                trusted customer registry loader
  settings.py                environment and repository allowlist
  versions.py                schema, parser and policy versions
  snapshot.py                command line entry point
  data/customer_registry.yaml  identity only, no caps, no expected answers
demo_contracts/              8 synthetic contract PDFs
evals/ground_truth/          hand written test oracle, never read at runtime
tests/                       resolution, fixture consistency, evidence snapshots
prep/                        pre build setup and integration smoke tests
docs/                        architecture, evaluation scenarios, runbook
```

Two separations matter, and both are enforced by tests.

`evals/ground_truth/` holds the expected answers, and nothing under `clauseci/`
may read or import it. A snapshot is also checked for oracle terms, so an
expected verdict cannot leak into evidence.

Provider adapters are read only. A test greps the runtime package for status
writes, Slack posts, Gmail drafts and non GET HTTP calls, and fails if one
appears.

The trusted source manifest holds provenance only. Whether a document may
establish a current obligation is derived in code from its execution status,
effective date and customer association, so that judgement is never stored as
data. Guard tests fail if a field or value in either data file starts to look
like an answer.

## Integration target

[`Pranavsingh431/clauseci-demo-saas`](https://github.com/Pranavsingh431/clauseci-demo-saas)
is the repository ClauseCI acts on. Its `main` branch is protected and requires
the `ClauseCI / retention-compliance` status, so a pull request there cannot be
merged until ClauseCI has reported.

| PR | Change | In scope |
|---|---|---|
| 1 | application and diagnostic retention 30 to 90 | yes, the hero case |
| 2 | in memory query cache | yes, as a negative case |
| 3, 4, 5 | sub processor, API deprecation, data residency | no, out of scope for this build |

## Running

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp .env.example .env      # then fill in your own credentials
./.venv/bin/python -m pytest tests/ -q
```

Build an evidence snapshot for a pull request. This reads GitHub and Google
Drive and writes nothing to either.

```bash
./.venv/bin/python -m clauseci.snapshot --pr https://github.com/Pranavsingh431/clauseci-demo-saas/pull/1
./.venv/bin/python -m clauseci.snapshot --pr 1 --save    # also writes JSON under runs/
```

Extract the retention obligations the signed agreements actually establish. This
calls the model, and still writes nothing to any provider.

```bash
./.venv/bin/python -m clauseci.obligations --pr 1
./.venv/bin/python -m clauseci.obligations --pr 1 --no-cache --save
```

Measure the high risk semantic cases against the hand written oracle, with the
cache disabled so every pass is a fresh reading.

```bash
./.venv/bin/python evals/semantic_repeatability.py --repeats 3
```

Produce the scoped release decision and the correction candidates. Still read
only, and the correction is never applied.

```bash
./.venv/bin/python -m clauseci.decide --pr 1
./.venv/bin/python -m clauseci.decide --pr 1 --save
```

Run the whole workflow. Read only unless you ask for writes.

```bash
./.venv/bin/python -m clauseci.run --pr 1              # analyse and print, writes nothing
./.venv/bin/python -m clauseci.run --pr 1 --execute    # publish to GitHub and Slack
```

Inspect what was intended and what was confirmed, or resolve anything left open.

```bash
./.venv/bin/python -m clauseci.state inspect --pr 1     # the whole case lifecycle
./.venv/bin/python -m clauseci.state evidence --pr 1    # sanitized lifecycle summary
./.venv/bin/python -m clauseci.state reconcile --pr 1
```

Produce the correction for a developer to apply. This writes nothing to any
repository.

```bash
./.venv/bin/python -m clauseci.correction --pr 1 --save
```

`check.sh` verifies every provider integration, including Gmail. Unlike the
snapshot command it does write to each provider and then delete what it wrote,
so it is a connectivity check rather than part of the product.

`check.sh` proves connectivity by writing to each provider and then reading the
result back. It needs `.env`, `credentials.json` and `token.json`, none of which
are in this repository.
