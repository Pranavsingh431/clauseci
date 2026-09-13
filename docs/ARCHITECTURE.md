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

## The scoped release decision

Everything from here is deterministic. No model is involved. The decision takes
effective configuration values from the snapshot and validated obligations from
the analyzer, and applies one predicate: for a customer and a category, is the
effective retention value at most the represented cap.

### Four states

| State | Meaning |
|---|---|
| `PASS_SCOPED` | every represented supported predicate passed, for this exact configuration and this exact source snapshot |
| `CONFLICT` | at least one represented supported predicate is violated |
| `REVIEW_REQUIRED` | evidence is missing, identity is ambiguous, extraction failed, or a changed file looks retention related but is not understood |
| `NO_SUPPORTED_CHANGE` | the changed file inventory is known, no supported surface changed, and nothing retention like is unrecognised |

Precedence is CONFLICT, then REVIEW_REQUIRED, then NO_SUPPORTED_CHANGE, then
PASS_SCOPED. A confirmed violation is the strongest statement available and is
never softened into review required, but whatever was also unclear is still
recorded in coverage and warnings.

`PASS_SCOPED` is not a claim of legal compliance. It says the represented
supported retention predicates passed for one recorded configuration against one
recorded source snapshot. Nothing wider.

### Four dispositions

`SATISFIED`, `VIOLATED`, `NO_REPRESENTED_OBLIGATION`, `REVIEW_REQUIRED`.

**`NO_REPRESENTED_OBLIGATION` is not `SATISFIED`.** It means the reviewed sources
establish no cap for that category. No limit is inferred from that, in either
direction. Acme Labs audit retention is exactly this case, and a test fails if it
ever acquires a numeric limit.

There is no separate disposition for an unchanged field. Change awareness is
carried by `changed_by_pr`, `present_in_base`, `present_in_head`,
`introduced_by_pr` and `resolved_by_pr`, which is enough to tell an introduced
problem from one that was already there, and from one this change fixes.

### Change awareness

Both revisions are evaluated against the same bound evidence. That is what lets
a conflict introduced by this pull request be told apart from a baseline conflict
it merely inherited, and from one it resolves. A change that fixes one field but
leaves another over its cap is still CONFLICT.

### Three candidates

| Candidate | Construction |
|---|---|
| A requested | the configuration exactly as proposed |
| B baseline relevant fields | every field the change moved, returned to its baseline value |
| C customer scoped correction | the request kept wherever the obligations permit it, with only the violating customer and category values pinned to baseline |

No customer name and no retention number appears in the constructors. Candidate
C is derived from the actual violations, the baseline and the obligations, so a
different represented cap produces a different result without any code change.

Every candidate is recomputed into effective values and put through the same
evaluator as the actual head. A constructor that intended to be safe proves
nothing. Candidate feasibility is `FEASIBLE_IN_SCOPE`, `CONFLICT` or `UNKNOWN`.
It is deliberately never called `PASS_SCOPED`, because that name belongs only to
a configuration that actually exists.

### Usefulness and ranking

The requested outcome set is frozen from the actual change before any candidate
is built: every customer and category whose effective value moved, together with
the value the change asked for. A candidate preserves an outcome when it produces
that same value.

Ranking, in order: reject anything that conflicts; never rank unresolved coverage
above a known feasible candidate; prefer the most requested behaviour preserved;
tie break on fewer changed fields against the requested head; tie break on
candidate id so the order never depends on dictionary order.

The result is described as the preferred supported candidate among the evaluated
alternatives. It is not claimed to be globally optimal, the safest possible
configuration, or the best legal solution.

### A candidate is a proposal

The actual head decision and the preferred candidate are separate fields and are
computed independently. **A safe candidate never turns an unsafe head green.**
For the hero change, the head is CONFLICT and candidate C is FEASIBLE_IN_SCOPE at
the same time, and that is the correct pair of statements.

The correction is rendered as proposed YAML and a unified diff. It is never
applied, committed or pushed. The renderer edits lines so the patch stays
minimal, then re-parses the result and checks it reproduces the candidate's own
effective values before handing it over.

### Coverage

Every decision records which surfaces were checked, which customers and
categories were evaluated, which categories have no represented cap, which
changed files were unsupported or unrecognised, the corpus digest and the head
SHA. It is bound to the exact head SHA, the exact corpus digest, and the parser,
policy, decision policy, semantic model and prompt versions.

    python -m clauseci.decide --pr <pull request URL>

## Bounded execution

Writes happen only when a person asks for them. The product command is read
only by default and needs `--execute` before it touches anything.

Every write is a deterministic consequence of a decision that was already
computed. No model is consulted during execution, and no model object is ever
handed a write adapter. A test asserts the semantic client takes an API key and
a model name and nothing else.

### Order

    analysis complete
      build execution plan
      freshness recheck
      GitHub status write
      GitHub read back and verify
      Slack create or update case
      Slack read back and verify
      seal the execution receipt

### Action policy

| Decision | GitHub status | Slack |
|---|---|---|
| `PASS_SCOPED` | success | no case |
| `CONFLICT` | failure | create or update one case |
| `REVIEW_REQUIRED` | failure | create or update one case |
| `NO_SUPPORTED_CHANGE` | success | no case |

`REVIEW_REQUIRED` publishes failure rather than error. Error reads as a broken
check, and this is a working check reporting that a person needs to look.

The status context is exactly `ClauseCI / retention-compliance`, defined once in
`versions.py`. Branch protection on the demo repository requires that string, so
the spelling is load bearing. `ClauseCI / smoke-test` belongs to the pre build
connectivity script and is never used for a product decision.

### Case identity

A case id is derived from the namespace, the repository and the pull request
number. **The head SHA is deliberately not part of it**, so a new commit
continues the same engineering case instead of opening a second one. The id
appears in the Slack message as `clauseci-case:<id>`, which is what read back
looks for.

Analysis identity is separate and does move with the head SHA, the corpus digest
and every version that could change the answer.

### Freshness

Immediately before the first write, the pull request head is re-fetched and
compared exactly, and the Drive corpus is checked through provider revision
metadata. If either moved, nothing is written and the receipt records
`SUPERSEDED_BEFORE_EXECUTION` or `SOURCE_CHANGED_BEFORE_EXECUTION`.

### Verification

Both providers are read back after the write. GitHub is checked for the exact
repository, commit, context, state and description. Slack is checked for the
case marker, repository, pull request number, head SHA, customer, decision, the
actual value, the represented limit, the source and the preferred correction.

An unrelated ClauseCI message is not accepted just because it exists.

A receipt is `VERIFIED` only when every required effect was observed with
matching fields. **GitHub alone is not success for a conflict.** If the status is
written and the Slack case cannot be confirmed, the receipt is not verified. It
is `UNKNOWN` when the outcome cannot be established, and `PARTIAL` when part of
the work definitively did not complete.

### What is not claimed

Not exactly once execution. There is no distributed transaction and no atomic
commit across two providers, and neither is claimed anywhere.

What does exist, added later and described in the reliability section below: a
durable journal of intended effects, restart recovery, lost response
reconciliation, and an explicit `UNKNOWN` state. Re-running on the same head
does not append a second identical GitHub status, because the latest record in
the context is adopted when it already says what this analysis intends.

Gmail is verified infrastructure and is not part of this workflow.

    python -m clauseci.run --pr <pull request URL>
    python -m clauseci.run --pr <pull request URL> --execute

## Reliability model

The honest statement of what this gives you:

> ClauseCI records intended effects before execution, reconciles uncertain
> writes against provider state, and refuses blind retries when a prior outcome
> cannot be established.

It is not exactly once. There is no distributed transaction and no atomic commit
across two providers. Those things are not available here and are not claimed.

### Stable case, versioned analysis

A case is one engineering conversation about one pull request. Its id comes from
the namespace, the repository and the pull request number, and **never** the head
SHA, so a new commit continues the same case. One case holds many analyses. An
analysis id moves with the head SHA, the corpus digest and every version that
could change the answer.

### Intent before action

Before any provider is called, the intended effect is written to a local SQLite
journal and committed. The effect key is derived from the case, the analysis, the
provider, the action, the target and the digest of the intended payload. The same
intent always derives the same key, and a changed payload derives a different one.

That ordering is the point. If the process dies between the intent and the
confirmation, the intent is still on disk and the next run goes and looks at the
provider instead of guessing.

### Effect states

`PLANNED`, `IN_FLIGHT`, `VERIFIED`, `UNKNOWN`, `FAILED`, `SUPERSEDED`, with an
explicit transition table. Illegal transitions raise rather than being written.
`VERIFIED`, `FAILED` and `SUPERSEDED` are terminal. Leaving `IN_FLIGHT` for
`SUPERSEDED` needs the caller to state that the external outcome is independently
understood.

A `PLANNED` effect never reached the provider, so it is never marked `UNKNOWN`.
Untried is not uncertain.

### The write before confirmation risk

The dangerous window is between the provider accepting a write and this process
recording it. A crash there leaves a real external change with no local record.

When that happens the effect is `UNKNOWN`, which is never collapsed into
`FAILED`. The difference decides whether a retry is safe, so the distinction is
kept even though it makes the output less tidy.

### Reconciliation

On the next run, unfinished effects are inspected against provider state. Bounded
by local state, never by sweeping provider history.

For Slack, identity resolution is ordered. The stored resource id is the normal
path, because it is an exact lookup. The channel search by case marker is
**recovery**, used when the id was never recorded. One match is adopted. Zero
means the write did not land. More than one is ambiguity, and ClauseCI refuses to
add a third and asks for a person.

For GitHub, commit statuses are history bearing and several records in one
context are normal, so uniqueness is not enforced. What is checked is whether the
latest record in the context already equals the intended state. If it does the
effect is adopted rather than written again, which is what stops a restart loop
appending an endless column of identical statuses. A genuinely new intent, with a
different payload, still appends a new record.

### Three freshness boundaries

Before the first effect, between effects, and again before sealing. If the head
or the corpus moved before anything was written, nothing is written at all. If it
moved after an effect landed, that effect stays in the audit record and the
workflow is `SUPERSEDED`. It is never reported as a current verified result, and
the Slack case continues to show the SHA it actually analyzed.

### Human edits

Before updating an existing case, the current text is compared against the last
payload ClauseCI verified and against the new intended payload. If it matches
neither, a person has edited it and ClauseCI refuses rather than overwriting
content it did not write. There is no merge algorithm, deliberately.

### Concurrency

One local lock row per case, taken with an immediate transaction. A second local
process cannot execute the same case at the same time. A lock past its expiry is
taken over, so a crashed process cannot block a case forever. This is a local
lock. It does not attempt distributed coordination.

    python -m clauseci.state inspect --pr 1
    python -m clauseci.state reconcile --pr 1

## The correction lifecycle

One case spans many commits. A new head is a new analysis, never a rewrite of
the old one.

### What ClauseCI does and does not do

ClauseCI proposes a correction. A developer applies it. **ClauseCI does not
commit, push or merge code.** A test greps the whole runtime package for
`git push`, `git commit`, `subprocess` and `os.system` and fails if any appear.
The write adapters contain no merge operation.

### The hero lifecycle, as it really ran

| | unsafe commit | corrected commit |
|---|---|---|
| head | `ed423b0b` | `a47f5657` |
| analysis | `an-988da41c` | `an-cc89f6f7` |
| decision | CONFLICT | PASS_SCOPED |
| GitHub status | failure | success |
| receipt | VERIFIED | VERIFIED |

Same case `cci-612892eccea4`. Same Slack resource. The unsafe commit keeps its
failing status forever, and the corrected commit earned its own.

### A pass is context sensitive

This is the Phase 7 change. A clean pass on a pull request nobody raised a case
about stays quiet. A pass on a pull request that already has an open case must
**close** it, or the case sits open forever claiming a problem that is gone.

| Decision | Existing case | GitHub | Slack |
|---|---|---|---|
| PASS_SCOPED | none | success | nothing |
| PASS_SCOPED | open | success | resolve the existing case, required |
| CONFLICT | any | failure | open or update |
| REVIEW_REQUIRED | any | failure | open or update |
| NO_SUPPORTED_CHANGE | any | success | nothing, ever |

`NO_SUPPORTED_CHANGE` never resolves anything. Not touching the retention
configuration is not evidence that an earlier conflict was fixed.

### Closure is verified, not assumed

A resolution updates the existing Slack resource by its recorded id. It never
opens a second root case, and a resolution that cannot find an existing case is
refused rather than turned into a new message. The case only moves to `RESOLVED`
after the closure has been read back out of Slack with the correct case id, both
commit SHAs and the current decision present.

The Phase 6 human edit protection still applies. A case a person has edited is
not overwritten by a resolution, and the case stays open.

### Wording

The resolved message says the correction was applied by a developer commit, and
that ClauseCI proposed it and confirmed the result but did not change the code.
Tests assert that phrases like "auto-remediated" and "fixed automatically" never
appear.

    python -m clauseci.correction --pr 1 --save
    python -m clauseci.state inspect --pr 1
    python -m clauseci.state evidence --pr 1

## Model routing

Verified working on OpenRouter with strict JSON schema output.

| Step | Model |
|---|---|
| Obligation interpretation | `anthropic/claude-sonnet-4.5` |
| Fallback if rate limited | `anthropic/claude-haiku-4.5`, `google/gemini-2.5-flash`, `openai/gpt-4.1-mini` |

Extracted contract text is cached per run. The 8 PDFs are not re downloaded for
each model call.


## Evaluation architecture

Three modes, kept visibly separate everywhere, because conflating them is how a
reliability claim becomes misleading.

| Mode | What it touches | What it proves |
|---|---|---|
| SEMANTIC | real model, local contract fixtures | the model read the right obligation from the right source |
| DECISION, KERNEL, LIFECYCLE | nothing external, fake providers, injected faults | deterministic code decided correctly and recovered correctly |
| LIVE | real GitHub, Google Drive and Slack, read only | the external effects actually happened |

A locally fault injected result is never labelled LIVE. A real provider read is
never described as simulated.

Raw records are written first, as JSON lines. Every summary number is computed
from those records by `evals/metrics.py`. A summary can be rebuilt from saved
raw evidence with `--summarize`, so the numbers are reproducible without paying
for the model again.

The metric that matters most is the false green count, reported with its
denominator: unsafe or unresolved cases that came back releasable. A metric
reported without its denominator can hide the size of the sample, so every ratio
here carries both numbers.

Safe case completion exists to stop the opposite failure. An agent that blocked
everything would score perfectly on false greens and be useless.

## Product feature freeze

The product feature set is frozen as of Phase 8. Production changes are limited
to false green bugs, broken provider verification, a broken demo path, a
security issue, or a submission blocking defect. No new capabilities.
