# ClauseCI

A customer aware release decision agent.

A pull request raises log retention from 30 days to 90 days. Two lines, in one
`defaults` block. It looks harmless, and for two of three tenants it is. For the
third it breaches a signed data processing amendment that lives in a Google
Drive folder nobody on the pull request has open.

ClauseCI resolves the effective retention value for every customer, reads the
controlling signed agreement, and records a decision on the pull request.

## Status

Phase 1 complete. The hero fixture and the supported scope are frozen, and
effective configuration resolution is implemented and tested. The analyzer, the
action layer and the evaluation runner are not built yet.

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
  config_resolution.py       deterministic effective value resolution
  data/customer_registry.yaml  identity only, no caps, no expected answers
demo_contracts/              8 synthetic contract PDFs
evals/ground_truth/          hand written test oracle, never read at runtime
tests/                       effective resolution and fixture consistency
prep/                        pre build setup and integration smoke tests
docs/                        architecture, evaluation scenarios, runbook
```

The separation that matters: `evals/ground_truth/` holds the expected answers
and nothing under `clauseci/` is allowed to read it. A test enforces that.

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
./check.sh                # verifies every provider integration
```

`check.sh` proves connectivity by writing to each provider and then reading the
result back. It needs `.env`, `credentials.json` and `token.json`, none of which
are in this repository.
