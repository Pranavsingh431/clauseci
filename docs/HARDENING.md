# Hardening report

Engineering evidence for the frozen build. Not a security report and not
marketing. Everything here was measured, and the evaluation numbers come from
`evals/results/latest-summary.json`, which is generated from raw records.

Runtime commit at the time of hardening: `ed7252a8d8dbc0f36ef3d0370ccdc5eee4ee80d7`

## Regression and evaluation

| Check | Result |
|---|---|
| pytest regression suite | 441 passed |
| Core evaluation, no provider mutation | 65 records, 65 passed |
| Full evaluation including live read only confirmation | 70 records, 70 passed |
| **False greens** | **0 of 11 unsafe or unresolved cases** |
| Safe case completion | 5 of 5 |
| Recovery | 5 of 5 recoverable fault cases |
| Verified live workflows | 2 of 2 |

The pytest count is engineering evidence. It is not an evaluation score and the
two are never added together.

## Metric audit

`python -m evals.audit` recomputes the headline denominators straight from the
raw records, without using the metrics module, and fails if the two disagree.
It passes.

The false green denominator is 11. It counts records whose expected release
decision is CONFLICT or REVIEW_REQUIRED. Scenario D08 is deliberately outside
it: candidate A's feasibility is also spelled CONFLICT, but a candidate is a
hypothetical configuration that is never released, so it is not a release
decision. The audit script records that exclusion by name so it cannot become
an accident.

The safe completion denominator is 5 and counts only records expected to be
PASS_SCOPED. NO_SUPPORTED_CHANGE is not counted as a pass.

## Provider verification

23 of 23 content checks are present, and no verifier accepts mere existence.

GitHub read back compares repository, commit SHA, exact status context, state
and description. Slack read back compares the case marker, repository, pull
request number, head SHA, decision, customer, represented limit, actual value,
source and preferred correction, and for a resolution also the previous head
SHA, the RESOLVED state and the developer attribution line. Drive compares
content digest, extracted text digest, folder membership and provider revision
metadata.

## Clean install

A fresh virtual environment, `pip install -r requirements.txt`, then imports,
the test suite and CLI help. 354 passed, 14 deselected. The deselected tests are
the CLI error path tests, which invoke the project's own `.venv` by path.

No runtime source depends on a local machine path. `git grep "/Users/"` over
`clauseci/` returns nothing.

## Secrets

No match for any credential pattern in the tracked tree or in the full git
history of this repository. `.env`, `credentials.json`, `token.json`, the
runtime SQLite database and `runs/` are all ignored, and none has ever been
committed.

## Error paths

A known failure prints one clear line and exits non zero. A mistyped pull
request reference, a repository outside the allowlist and a missing credential
all report the problem without a stack trace and without printing any value.
`CLAUSECI_DEBUG=1` restores the traceback.

The product command is read only by default. `--execute` is the only way to
reach a provider write, and the help text says so.

## Latency

One read only run on the corrected commit: snapshot 11.45s, semantic 0.06s on a
warm cache, decision 13 ms, total 12.60s. A cold semantic pass measured about 40
seconds in earlier phases. One sample each. No percentiles are claimed.

## The blank public page

The deployed console rendered for one visitor and then served an empty dark
shell to everyone else. The cause was ours, and it was in four lines.

`ui/console.py` drew the entire page at import time, and `streamlit_app.py`
did nothing but `import ui.console`. Streamlit re-executes the entry script on
every rerun and on every new browser session, but `sys.modules` lives for the
whole life of the server process. So the second `import ui.console` was a no
op. The first visitor after a start rendered the console. Every visitor after
that ran a script that did nothing and got a blank page. A reboot appeared to
fix it, which is what made the failure look intermittent and made the proxy,
the runtime and the CDN all look guilty.

The page now renders from `ui.console.main()`, which the entry point calls on
every run.

Measured on Streamlit's own Python 3.14.7 stack, driving real websocket
sessions against a live server exactly as a browser tab does, counting the
elements actually delivered:

| Code | Sessions that rendered |
| --- | --- |
| Before the fix | 1 of 10 |
| After the fix | 10 of 10 |

Only the first session before the fix rendered, and it delivered 193 elements
where the other nine delivered zero. DEP09 and DEP10 hold the fix: DEP09 draws
the page twice in one process and requires the second render to match the
first, DEP10 requires the entry point to call a function rather than lean on an
import. Both fail against the previous code.

## Public deployment runtime

The local environment runs Python 3.10.12. Streamlit Community Cloud runs
Python 3.14.7, four minor versions ahead, and resolves pandas 3.0.5 where the
local environment has pandas 2.3.3.

That gap was the first suspect, so it was tested rather than assumed. The
console was built into a Python 3.14.7 environment from the same
`requirements.txt` and run through Streamlit's AppTest harness: the script
completed with zero exceptions, rendering 102 markdown blocks, 6 dataframes
and 9 tabs. Served over HTTP on that stack it returns 200 and its health
endpoint reports ok.

The runtime is compatible and no Python version pin is needed. The public
app's imports are `json`, `pathlib`, `sys` and `streamlit`, so it does not
exercise the heavier pinned dependencies at all. Clearing the runtime early is
what left the import side effect as the only remaining explanation.

## Public deployment access

Separate from the rendering defect, the deployed app answers an anonymous
request with `HTTP 303` to `share.streamlit.io/-/auth/app`. `/_stcore/health`
and `/_stcore/stream` redirect the same way, so an anonymous viewer's websocket
never opens. That is a viewer access setting on the deployment, not a property
of this code, and it is changed by a human in the Streamlit Cloud dashboard.
The same endpoints on a local server on the same stack return 200 and ok.

## Known limitations

- One obligation family, RETENTION_UPPER_BOUND, over three log categories.
- One synthetic contract corpus and a cohort of three customers.
- Kernel and lifecycle evidence comes from local fault injection, not real
  provider outages.
- Live evidence is read only confirmation of state the real runs produced.
- The execution lock is local. It does not coordinate across machines.
- Slack marker recovery scans a bounded slice of channel history. It is the
  recovery path, not the normal one.
- A generic provider exception maps to UNKNOWN, so some definitively failed
  writes read as uncertain. That is the conservative direction.

## Feature freeze

PRODUCT FEATURE FREEZE REMAINS ACTIVE. Production changes are permitted only for
a false green, a broken demo path, a secret or security issue, broken provider
verification, or a submission blocker.
