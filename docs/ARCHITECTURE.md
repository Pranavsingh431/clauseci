# ClauseCI architecture

Scope frozen at the start of the build window. Do not widen it mid build.

## Supported domain

Customer specific log retention configuration. Nothing else.

Supported fields:

| Field | Judged when |
|---|---|
| `application_logs_days` | always |
| `diagnostic_logs_days` | always |
| `audit_logs_days` | only where the controlling clause explicitly names audit logs |

`backup_retention_days` is parsed but not judged. Data residency, sub processor
notice and API deprecation notice are out of scope for this build. The contract
documents still contain those clauses. The agent must ignore them rather than
guess at them.

## External apps

| App | Role | Access |
|---|---|---|
| GitHub | the proposed change, and where the decision is recorded | read pull request and diff, write commit status |
| Google Drive | the contract evidence | read only |
| Slack | the engineering case | post and read back in one channel |

Gmail is optional infrastructure. It is verified to work, but it is not part of
the primary workflow and must not be added to it in this build.

## Shape

```
pull request number
      │
      ▼
[1] GitHubReader            pull request, changed files, unified diff, head SHA
      │
      ▼
[2] ConfigResolver          deterministic, no model involved
      │                     parse retention.yaml at base and at head
      │                     resolve effective value per (customer, field)
      │                     diff them, so a two line change to defaults
      │                     becomes three separate customer changes
      │
      ▼
[3] DriveReader             deterministic, read only
      │                     registry says which documents belong to which
      │                     customer, download, extract text with pypdf
      │
      ▼
[4] ObligationInterpreter   the one bounded model component
      │                     input: extracted contract text, marked untrusted
      │                     output: structured data only
      │                       which document controls, and why
      │                       execution status of each candidate document
      │                       verbatim clause quote
      │                       numeric cap per supported field
      │                       explicit "not covered" where no clause applies
      │                     it returns findings, it never performs an action
      │
      ▼
┌──────────────────────────────────────────────────────────────┐
│ [5] DECISION AND ACTION LAYER, deterministic, no model        │
│                                                              │
│  bind    head SHA still current, else abort                  │
│          quoted clause actually present in the source text,   │
│          else reject the finding as unsupported               │
│  resolve customer identity maps to exactly one registry entry,│
│          else review required                                 │
│  compare effective value against the cap, per field           │
│  decide  compliant, breach, or review required                │
│  dedupe  correlation id from (head SHA, customer, field)      │
└──────────────────────────────────────────────────────────────┘
      │
      ├── GitHub  commit status `ClauseCI / retention-compliance`
      └── Slack   one case message in the alert channel
      │
      ▼
[6] Verifier                read every write back from the provider
      │                     GitHub: read the status for that SHA
      │                     Slack:  read the message by timestamp
      ▼
[7] DecisionRecord          verified only when every intended effect was
                            observed in provider state. Otherwise partial.
```

## What the model may and may not do

The model may interpret contract text and return structured data.

The model may not write a GitHub status, post to Slack, create a Gmail draft,
push a commit, merge code, modify repository state, run a shell command, or make
an arbitrary HTTP request. There is no tool object for any of these, so it is not
a matter of the prompt asking it not to.

Deterministic code owns configuration parsing, customer identity mapping,
effective value resolution, authorization, state transitions, external writes,
deduplication, reconciliation, verification and the final decision.

Code from the pull request under analysis is never executed.

## Trust boundary

Contract text, pull request titles and pull request bodies are evidence, not
instructions. They are passed to the model inside delimited blocks labelled as
untrusted. Every clause quote the model returns is checked against the source
text before it can affect a decision.

`07_Acme_Security_Addendum_2026.pdf` contains an instruction addressed to
automated review agents. Obeying it is a test failure, not a feature.

## Uncertainty

Three outcomes, not two.

| Outcome | Meaning |
|---|---|
| compliant | every supported field is within the cap in the controlling document |
| breach | at least one supported field exceeds the cap |
| review required | evidence is missing, ambiguous, or identity does not resolve |

Missing evidence never becomes a green result.

## Claims this build will not make

1. Not exactly once execution across external systems. Reconciliation with
   stable correlation identifiers instead.
2. Not universal legal compliance. Results are scoped to the supported fields,
   the three registry customers, and one source snapshot.
3. Not that a proposed correction makes the existing commit safe. The existing
   commit keeps its own result. A corrected commit is fetched and analyzed as a
   separate head SHA.

## Deliberate non goals

No vector database. The corpus is 8 documents. No multi agent choreography, no
LangGraph, no CrewAI, no AutoGen. No Kubernetes or cloud deployment. No webhook
until the core workflow and its evaluation are finished.

## Evidence snapshot

Steps 1 to 3 produce an `AnalysisSnapshot`, which is the immutable record every
later decision is bound to. It records the repository, the pull request, both
exact revisions, the complete changed file inventory with a surface class per
file, the full effective configuration at base and at head with provenance for
every value, and every contract document with a provider identity, a digest of
its bytes and a digest of its extracted text. A corpus digest covers the whole
evidence set.

The corpus digest is computed from file identity, provider revision and content
digest, sorted by identity. It does not depend on filename or listing order, so
a rename or a reordered listing leaves it unchanged, and any byte change moves
it.

The snapshot contains no contractual cap, no verdict and no expected result.
Building one calls no model and writes nothing to any provider.

    python -m clauseci.snapshot --pr <pull request URL>

## Obligation analysis

Step 4 is the only place a model is used. It reads contract language and returns
structured data. It is given no tools, so it cannot write a status, post a
message, call an API or take any action on anything it reads.

The work is split so the model never holds authority it cannot be checked on.

| Owned by the model | Owned by deterministic code |
|---|---|
| what a clause says | which customer a finding belongs to |
| the number and unit in the text | which documents belong to that customer |
| which categories a clause names | whether a source may establish a current obligation |
| which of two competing clauses the language says controls | whether the quote is real, and whether it states the claimed number |
| | the schema, the unit, the operator, the category allowlist |

### Extraction

One document at a time, one customer at a time. The whole corpus is never
handed over with the question "which contract controls". Each document produces
zero or more candidate clauses, each with a verbatim quote.

The model receives the customer identity as a task field. It is told explicitly
that retrieved text is evidence, and that instructions found inside a document,
a pull request or a filename carry no authority.

A document with no supported retention clause returns NO_SUPPORTED_OBLIGATION
and an empty list. That is a correct answer, not a failure. Language that cannot
be represented without guessing returns AMBIGUOUS.

### Validation

Every candidate is checked before it can affect anything:

* the customer exists in the registry
* the source belongs to that customer, in both the manifest and the registry
* the file id and content digest match the snapshot
* the operator is `<=`, the unit is days, the value is a positive whole number
* every category is on the allowlist
* the quote occurs verbatim in the extracted text
* the quote actually states the number being claimed

The last two are separate checks and neither implies the other. A sentence can
be genuinely present in a document and still say nothing about retention. The
injection passage in `07_Acme_Security_Addendum_2026.pdf` is exactly that case:
it quotes cleanly and supports no number.

**Quote verification proves evidence binding, not legal correctness.** It shows
the text came from that document. It does not show the reading is right. That
limit is real and is not claimed away anywhere in this system.

### Authority

Deterministic elimination runs first. A source belonging to another legal
entity, not executed, not yet effective, or outside the customer's corpus cannot
establish a current obligation. No model is consulted for that decision. The
source stays in the record as a rejected source with its reasons.

Only when more than one eligible clause still competes for the same category is
a second bounded semantic step used. It sees the candidate clauses, not whole
documents.

Resolution runs per category. An amendment that replaces application and
diagnostic retention does not erase an audit clause unless its language says so.

### Three outcomes

`RESOLVED`, `NO_REPRESENTED_OBLIGATION`, `REVIEW_REQUIRED`. Missing, ambiguous or
unverifiable evidence becomes REVIEW_REQUIRED or NO_REPRESENTED_OBLIGATION. It
never becomes a pass. Phase 3 produces no pass or fail for the pull request at
all. That is a later phase.

### What is not claimed

This is not general contract understanding. It supports one obligation family,
RETENTION_UPPER_BOUND, with operator `<=`, unit days, over three log categories,
for a fixed customer cohort. Residency, sub processors, notice periods, service
levels and security controls are out of scope, and the documents contain such
clauses precisely so the analyzer has to leave them alone.

    python -m clauseci.obligations --pr <pull request URL>

## Model routing

Verified working on OpenRouter with strict JSON schema output.

| Step | Model |
|---|---|
| Obligation interpretation | `anthropic/claude-sonnet-4.5` |
| Fallback if rate limited | `anthropic/claude-haiku-4.5`, `google/gemini-2.5-flash`, `openai/gpt-4.1-mini` |

Extracted contract text is cached per run. The 8 PDFs are not re downloaded for
each model call.
