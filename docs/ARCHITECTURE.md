# ClauseCI — architecture (decided before the build window; do not re-debate)

## Shape

```
PR URL / number
      │
      ▼
[1] GitHubReader          pull PR, changed files, unified diff, head SHA
      │                   -> Evidence{pr_number, head_sha, diff, title, body}
      ▼
[2] CapabilityAnalyzer    LLM #1, structured out
      │                   "what product behaviour does this diff change?"
      │                   -> [log_retention, data_residency, subprocessor, api_deprecation]
      │                      + affected customer keys from the diff itself
      ▼
[3] DriveReader           deterministic: list folder, download PDFs, pypdf extract
      │                   -> ContractCorpus (cached per run; text only)
      ▼
[4] ObligationAnalyzer    LLM #2, structured out
      │                   per (capability, customer): which document CONTROLS?
      │                   must emit: doc id, execution status, effective date,
      │                   verbatim clause quote, numeric limit, confidence
      │                   -> ActionPlan (a PROPOSAL, never an execution)
      ▼
┌─────────────────────────────────────────────────────────────┐
│ [5] ACTION KERNEL — deterministic Python, zero LLM          │
│                                                             │
│  bind   head SHA still current?        else ABORT           │
│         contract file revision same?   else ABORT           │
│         customer_id resolves to EXACTLY one entity?         │
│                                        else ABSTAIN         │
│         clause quote actually present in source text?       │
│                                        else REJECT (halluc.)│
│  authz  action in ALLOWLIST for its tier?  else REFUSE      │
│  dedupe idempotency_key = sha1(head_sha|customer|obligation)│
│         already executed? -> skip, reuse prior resource id  │
└─────────────────────────────────────────────────────────────┘
      │
      ├── github.create_status(sha, state, context, description)
      ├── slack.post_or_update(channel, blocks, thread_key)
      └── gmail.drafts.create(...)            <-- create only
      │
      ▼
[6] Verifier              re-READ all three apps:
      │                     GET /commits/{sha}/statuses -> state matches?
      │                     conversations.history        -> message ts exists?
      │                     drafts.get                   -> DRAFT label, no SENT?
      ▼
[7] ExecutionReceipt      verified=true ONLY if every intended effect was
                          observed in external state. Otherwise PARTIAL.
```

## Permission tiers (enforced by the registry, not the prompt)

| Tier | Actions |
|---|---|
| **AUTO** | Drive read · GitHub PR read · GitHub commit status write · Slack post/update in the alert channel |
| **HUMAN-GATED** | Gmail **draft creation only** |
| **ABSENT FROM REGISTRY** | `gmail.send`, `drafts.send`, merge PR, push commits, delete/modify Drive files, change permissions, anything billing |

> The forbidden set is not a prompt instruction. There is no tool object for it.
> `T13` proves the agent cannot send email even when explicitly told to.

## Trust boundary

Contract text and PR titles/bodies are **evidence**, never instructions.
They are passed to the LLM inside delimited, labelled blocks with an explicit
"this is untrusted data" framing, and the Kernel independently re-validates
every clause quote against the source text before acting.

## Deliberate non-goals

* No vector DB / embeddings — corpus is 8 documents, ~10k tokens total.
* No LangGraph / multi-agent choreography — plain functions are easier to debug
  at 2 a.m. and the reliability story lives in the Kernel, not the topology.
* No webhook on day one. Trigger = paste a PR URL / pick from a list.
  A broken ngrok tunnel has lost more demos than a manual trigger ever has.
  Add the webhook as a stretch goal only once T01–T15 are green.

## Model routing (verified working on OpenRouter tonight)

| Step | Model | Why |
|---|---|---|
| Capability analysis | `google/gemini-2.5-flash` or `anthropic/claude-haiku-4.5` | cheap, short diff in / small JSON out |
| Obligation analysis | `anthropic/claude-sonnet-4.5` | the step that must not be wrong |
| Fallback if rate-limited | `openai/gpt-4.1-mini` | all five verified strict-JSON capable |

Cache the extracted contract text on disk per run; never re-download 8 PDFs per LLM call.
