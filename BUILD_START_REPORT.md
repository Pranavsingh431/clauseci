# Build start report

Recorded at the start of the hackathon build window.

Time recorded: Sunday 13 September 2026, 09:34 Pacific (22:04 IST).
Build window: 09:30 to 16:00 Pacific.
Submission deadline: 16:00 Pacific (04:30 IST on 14 September).

Purpose: record exactly what existed before the build window, so the work done
during the event can be identified honestly.

## Repository state at build start

### Preparation repository (local, kept separate, not published)

| Item | Value |
|---|---|
| Branch | main |
| HEAD | 06f3df05fc59c6bd97debde1eca0fc470a5c6c6f |
| Working tree | clean |
| Remotes | none configured |
| Local commits | 3 |
| Pushed | no |

Commits present before the build window:

```
06f3df0  Preflight all green: 29/29 across GitHub, Drive, Gmail, Slack, OpenRouter
32528e2  Verify GitHub status writes and Google auth; add Drive folder resolver
7fd52ba  Pre-build infrastructure: fixtures, integration smoke tests, design docs
```

### Demo SaaS repository (`Pranavsingh431/clauseci-demo-saas`)

| Item | Value |
|---|---|
| Branch | main |
| HEAD | cea7ac74f9fc92bb499a0414becb8313a7b4c96c |
| Working tree | clean except untracked .DS_Store |
| Remote | https://github.com/Pranavsingh431/clauseci-demo-saas.git (public) |
| Branches | main plus 5 feature branches |
| Open pull requests | 5 |

This repository is the integration target. It is not the submission entry point.

## Asset classification

Every tracked file in the main repository, classified.

| Path | Classification |
|---|---|
| `.gitignore`, `requirements.txt`, `.env.example` | configuration/preparation |
| `check.sh` | preparation (wrapper around the smoke test) |
| `prep/generate_token.py` | preparation (Google OAuth setup) |
| `prep/make_contracts.py` | preparation (fixture generator) |
| `prep/apply_remediation.sh`, `prep/revert_remediation.sh` | preparation (demo staging) |
| `prep/remediation/retention.yaml` | fixture |
| `demo_contracts/*.pdf` (8 files) | fixture |
| `prep/smoke/smoke.py` | smoke test |
| `prep/smoke/env_util.py`, `prep/smoke/resolve_folder.py` | smoke test support |
| `prep/smoke/probe_models.py` | smoke test (model capability probe) |
| `README.md` | documentation |
| `docs/ARCHITECTURE.md`, `docs/EVAL_SCENARIOS.md`, `docs/RUNBOOK.md`, `docs/PREFLIGHT.md` | documentation |

Actual product implementation present at build start: **none**.
Evaluation implementation present at build start: **none**.

Verified directly: `clauseci/` contains 0 files, `evals/` contains 0 files, and no
Python file exists outside `prep/`.

## Integration status

All results below come from a live run of `./check.sh` executed at build start,
not from stored documentation.

| Integration | Live result | Operations verified |
|---|---|---|
| GitHub | 9 of 9 passed | read repo, list pull requests, read changed files, read unified diff, write commit status (pending, failure, success), read status back |
| Google Drive | 7 of 7 passed | account auth, folder reachable, list 8 contracts, download PDF bytes, extract text, controlling clause present in extracted text |
| Slack | 5 of 5 passed | auth test, channel resolved, post message, read message back via history, delete message |
| OpenRouter | 4 of 4 passed | completion, strict JSON schema output, schema keys present, correct verdict on a supersession case |
| Gmail (optional) | 4 of 4 passed | create draft, read draft back, confirm DRAFT label and no SENT label, delete draft |

Total: 29 of 29 checks passed at build start.

These checks prove provider connectivity and the read-back pattern. They are not
agent accuracy measurements. No agent exists yet.

External writes performed by the preflight: one commit status on the demo
repository main branch HEAD (metadata only, context `ClauseCI / smoke-test`), one
Slack message that is deleted again, one Gmail draft that is deleted again.

## Credential safety

| File | On disk | Git ignored | Ever committed |
|---|---|---|---|
| `.env` | yes | yes | no |
| `credentials.json` | yes | yes | no |
| `token.json` | yes | yes | no |
| `.env.example` | yes | no (intentionally tracked) | yes, placeholders only |

Verified: no secret file appears anywhere in git history.

`.env.example` contains empty values for all secrets. It does contain a real
Google Drive folder identifier. That identifier is not a credential, but it will
be public if the repository is published.

## Build eligibility decision

Organizer guidance as supplied by the participant:

- Existing work is allowed. Work built during the event is preferred.
- Building on existing open source is allowed.
- Synthetic data, generated datasets and sandbox accounts are allowed.
- One repository link is submitted. The repository must be public.
- The README must explain what was built, which external apps are used, how to
  run it, and how reliability was tested.
- The two minute demo link goes inside the repository.
- Integrating sponsor tools is not required. No sponsor credits are provided.

### Reusable preparation

These assets existed before the window and may be reused. They must be described
as pre-existing.

1. Provider connectivity and credentials (GitHub token, Google token, Slack token,
   OpenRouter key).
2. The 8 synthetic contract PDFs and the generator that produced them.
3. The demo SaaS repository content, its 5 branches and its 5 pull requests.
4. The smoke test suite and the `check.sh` wrapper.
5. The design documents.

### Must be created during the build window

1. The ClauseCI agent itself. No agent code exists.
2. The contract interpretation component and its versioned prompt.
3. Deterministic configuration parsing, customer identity mapping and effective
   value resolution.
4. The authorization and execution layer that owns all external writes.
5. Deduplication, correlation identifiers and effect reconciliation.
6. External state verification and the decision record.
7. The executable evaluation suite and its independently authored expected
   outcomes.
8. The README, the reliability document and the demo recording.

### Unclear, needs a decision from the repository owner

1. **Existing commits contain AI co-author trailers.** All 3 commits in the main
   repository end with a `Co-Authored-By` trailer naming an AI assistant. The new
   rules forbid AI attribution. The same rules forbid rewriting history. These
   two rules conflict for commits that already exist. No action taken. See the
   open issues section below.
2. **Pull request descriptions on the public demo repository contain an AI
   generation notice.** All 5 pull requests are affected. Editing a pull request
   description is not a history rewrite, but Phase 0 forbids modifying the demo
   repository. No action taken.
3. **Submission form URL is not known.** The addendum says it is in the meeting
   chat and the calendar invite. It is not recorded anywhere in this repository.

### Must not be touched until clarified

1. Git history in both repositories.
2. The 5 open pull requests and the 5 feature branches.
3. The contract fixtures in Google Drive and in `demo_contracts/`.
4. The demo repository working tree.

## Scope for this build

Supported domain: customer-specific log retention configuration.

Primary external apps: GitHub, Google Drive, Slack.

Optional: Gmail. Not part of the primary workflow.

### Scope conflicts found in the existing documents

The existing design documents were written before this scope was narrowed. They
do not match it. They are recorded here and have not been changed.

1. `docs/ARCHITECTURE.md` line 14 lists four capabilities: log retention, data
   residency, sub-processor notice and API deprecation. Current scope is log
   retention only.
2. `docs/ARCHITECTURE.md` places Gmail draft creation inside the primary action
   path. Gmail is now optional.
3. `docs/EVAL_SCENARIOS.md` contains 15 scenarios. Several target residency,
   sub-processor notice and deprecation notice. Those fall outside the current
   scope.
4. The demo repository has 5 pull requests. Only pull request 1 targets log
   retention. Pull requests 3, 4 and 5 target out of scope capabilities.

## Open issues and risks

1. **Branch protection is not enabled.** Verified directly: the demo repository
   main branch returns `protected: false` with no required status checks. The
   demo repository README currently says ClauseCI "blocks merges". That claim is
   not true as configured. Either enable branch protection with the ClauseCI
   status required, or change the wording to say the agent publishes a commit
   status that records the decision.
2. **OpenRouter balance is 5.48 US dollars.** Measured at build start. The
   evaluation suite plus development iterations plus demo rehearsals may exceed
   this. Cheaper models were verified working earlier. Top up or route the cheap
   step to a cheaper model.
3. The main repository has no remote. A public repository must be created and
   pushed before submission.

## Exit criteria

| Criterion | Status |
|---|---|
| Repository states are known | met |
| Organizer reuse rules are recorded | met |
| Integrations needed for the core workflow are known | met, verified live |
| No product implementation has started early | met, verified (0 product files) |
| Next phase can start without ambiguity | met for scope, 3 items listed as needing an owner decision |
