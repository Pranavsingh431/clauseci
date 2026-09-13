# ClauseCI — build-day runbook

**Window:** Sun 13 Sep 2026, 09:30–16:00 PT  =  **22:00 IST Sun → 04:30 IST Mon**
Judging 16:00–16:40 PT (04:30–05:10 IST) · Awards to 17:00 PT (05:30 IST)

Sleep in the afternoon IST. You are building overnight.

---

## T-0: before you write a line (09:30–09:45 PT)

```bash
./check.sh
```
All green, or fix before anything else. Then read `docs/ARCHITECTURE.md` once.
**Do not redesign.** The design is settled.

---

## Schedule (PT · IST)

| PT | IST | Block | Done when |
|----|-----|-------|-----------|
| 09:45–10:15 | 22:15–22:45 | Skeleton: `clauseci/{config,github_app,drive_app,slack_app,gmail_app,llm,kernel,verify,run}.py`. Lift the working API calls straight out of `prep/smoke/smoke.py`. | `python -m clauseci.run --pr 2` prints PR diff + contract list |
| 10:15–11:15 | 22:45–23:45 | **[2] CapabilityAnalyzer** + **[3] DriveReader** with on-disk text cache | structured `{capabilities, candidate_customers}` for PRs 1–5 |
| 11:15–12:30 | 23:45–01:00 | **[4] ObligationAnalyzer** → `ActionPlan` w/ verbatim clause quotes | PR #1 yields Acme/30-day/violation; PR #2 yields empty plan |
| 12:30–13:30 | 01:00–02:00 | **[5] Kernel** (bind, authz, dedupe) + 3 executors + **[6] Verifier** | PR #1 end-to-end: red check, 1 Slack msg, 1 unsent draft, receipt `verified=true` |
| **13:30–13:50** | **02:00–02:20** | **FREEZE. RECORD THE BACKUP DEMO NOW.** | An MP4 exists on disk showing the happy path working |
| 13:50–14:50 | 02:20–03:20 | Eval runner over `docs/EVAL_SCENARIOS.md`, 15 scenarios × 3 | `evals/results.json` + printed table |
| 14:50–15:20 | 03:20–03:50 | Streamlit dashboard: PR picker, evidence panel, receipt, eval table | one screen a judge can read in 5 seconds |
| 15:20–15:40 | 03:50–04:10 | `RELIABILITY.md` from real numbers + README + push | repo public, README top-to-bottom correct |
| 15:40–16:00 | 04:10–04:30 | Record the final 2-min demo. Submit. | submitted **before** 16:00 PT |

---

## Hard rules

1. **13:30 PT backup recording is non-negotiable.** A working recorded demo beats
   a broken live one. If you fall behind, cut scope, not the recording.
2. **No webhook** until T01–T15 are green. It is a stretch goal, not a feature.
3. **No new integrations.** Four apps is the story; a fifth adds risk and no points.
4. **Never invent a metric.** `34/45` beats "highly reliable". If the runner
   didn't produce it, it doesn't go in the brief.
5. If a step blows its slot by >15 min, stub it and move on. Kernel + Verifier
   are worth more than a prettier ObligationAnalyzer.

## Cut list, in the order you cut

1. Streamlit dashboard → fall back to rich terminal output
2. T10 / T09 scenarios → keep T01–T08, T13–T15
3. PR #4 deprecation case → keep retention + residency + sub-processor
4. Gmail → **never cut**; the unsent draft is the strongest reliability beat

## Demo script (2:00)

| t | Screen | Say |
|---|--------|-----|
| 0:00 | GitHub PR #1 diff | "Reasonable change. 30→90 day log retention. It also silently breaches an enterprise customer's signed DPA." |
| 0:15 | ClauseCI running; Drive folder visible with 8 contracts | "It reads the diff, then the actual signed agreements. Three Acme documents disagree — one superseded, one signed, one unsigned draft." |
| 0:40 | GitHub check goes **red** | "Blocked. Acme Corporation, Amendment No.1 §2.1, 30 days. With the clause quoted and the document named." |
| 0:55 | Slack escalation | "One escalation. Evidence, not a vibe." |
| 1:10 | Gmail **DRAFT** | "Customer notice prepared — and unsent. The agent has no send-email tool. Not a prompt instruction: the tool does not exist." |
| 1:25 | `apply_remediation.sh` → re-run → check goes **green** | "Developer pins Acme back to 30. New SHA, full re-evaluation. Same thread updated. No duplicate email." |
| 1:42 | Eval table | "15 adversarial scenarios, 3 runs each. Every pass is verified by reading GitHub, Slack and Gmail back — never by asking the model whether it succeeded." |

No architecture slide. The diagram goes in the README.
