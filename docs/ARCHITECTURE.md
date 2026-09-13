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

## Model routing

Verified working on OpenRouter with strict JSON schema output.

| Step | Model |
|---|---|
| Obligation interpretation | `anthropic/claude-sonnet-4.5` |
| Fallback if rate limited | `anthropic/claude-haiku-4.5`, `google/gemini-2.5-flash`, `openai/gpt-4.1-mini` |

Extracted contract text is cached per run. The 8 PDFs are not re downloaded for
each model call.
