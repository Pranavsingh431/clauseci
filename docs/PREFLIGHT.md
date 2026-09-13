# Preflight — ALL GREEN (29/29)

Verified Sat 12 Sep 2026, 13:55 PT · Sun 13 Sep, 02:25 IST
Build window opens **22:00 IST tonight** (09:30 PT).

Re-run any time: `./check.sh` — or one service: `./check.sh github drive gmail slack openrouter`

## Verified against the real APIs

| Service | Checks | Result |
|---|---|---|
| **GitHub** | repo read · list PRs · changed files · unified diff · POST status pending/failure/success · **read status back** | 9/9 |
| **Google Drive** | account auth · folder reachable · list 8 contracts · download PDF bytes · pypdf extract · controlling clause present in extracted text | 7/7 |
| **Gmail** | `drafts.create` · **read draft back** · confirm `labels=['DRAFT']` and no `SENT` · delete | 4/4 |
| **Slack** | `auth.test` · channel resolved · `chat.postMessage` · **`conversations.history` read-back** · `chat.delete` | 5/5 |
| **OpenRouter** | completion · strict JSON-schema structured output · schema keys · correct verdict on the supersession trap | 4/4 |

Every write is verified by **reading external state back**, never by trusting HTTP 200.
That is the pattern the whole reliability story rests on — it is already proven end to end.

## Account / resource map

| Thing | Value |
|---|---|
| GCP project | `clauseci-hackathon`, Desktop OAuth client |
| Google account authorised | project owner account (Drive read-only plus Gmail compose, refresh token present) |
| Drive folder | `ClauseCI Demo Contracts`, owned by a second account belonging to the same person, with the authorised account added directly as Editor and general access set to Restricted |
| Slack | workspace `clauseci-alerts`, channel `#all-clauseci-alerts`, bot `@clauseci` invited (channel id is configured in `.env`) |
| GitHub PAT | fine-grained, single repo, Contents:R · PRs:R · **Commit statuses:RW** · Metadata:R |
| Demo repo | `Pranavsingh431/clauseci-demo-saas` — `main` + 5 open PRs |

## Fixtures in place

**8 contracts** in Drive, all `application/pdf` (not Docs-converted), all extract cleanly:

| File | Role |
|---|---|
| `02_Acme_DPA_2025` | 180 days, EXECUTED — **superseded distractor** |
| `03_Acme_DPA_Amendment_2026_SIGNED` | **30 days, EXECUTED — the controlling document** |
| `06_..._UNSIGNED_DRAFT` | 365 days, newest date, **NOT EXECUTED — must be ignored** |
| `07_Acme_Security_Addendum_2026` | contains a **prompt-injection payload** |
| `08_AcmeLabs_MSA_2026` | `Acme Labs Pvt Ltd` — **identity trap**, 180 days |
| `05_Globex_DPA_2026` | 90 days allowed — **must not false-positive** |
| `01`, `04` | MSAs carrying deprecation-notice and SLA terms |

**5 PRs**, covering four distinct obligation types plus injection — see README.

**Remediation pre-staged**: `bash prep/apply_remediation.sh` pushes the fix commit to
PR #1 (new SHA → forces re-evaluation). `prep/revert_remediation.sh` rewinds it so you
can rehearse the demo as many times as you like.

## One watch item

**OpenRouter balance $7.40.** ~100 Sonnet-4.5 runs; you need 45 eval runs plus rehearsals.
Top up to ~$25, or route the cheap step elsewhere — `anthropic/claude-haiku-4.5`,
`google/gemini-2.5-flash`, `openai/gpt-4.1-mini` and `deepseek/deepseek-chat-v3.1` all
passed the supersession trap with strict JSON tonight, at 4–25× less.

## Tomorrow, in order

1. `./check.sh` — expect 29/29
2. Read `docs/ARCHITECTURE.md` once. Do not redesign.
3. Follow `docs/RUNBOOK.md`. **Record the backup demo at 13:30 PT / 02:00 IST**, no exceptions.
