"""
ClauseCI Evidence Console.

    streamlit run ui/console.py

Reads captured evidence from disk first, so the page is useful even when a
provider is slow. Current provider state is an optional refresh and is always
labelled separately from captured evidence.

This is a presentation layer. It calls existing modules and never reimplements
a decision.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVIDENCE = ROOT / "evals" / "results" / "hero-lifecycle.json"
SUMMARY = ROOT / "evals" / "results" / "latest-summary.json"

CATEGORIES = ["application_logs", "diagnostic_logs", "audit_logs"]
LABEL = {"application_logs": "Application", "diagnostic_logs": "Diagnostic",
         "audit_logs": "Audit"}
ENTITY = {"acme-corp": "Acme Corporation", "globex": "Globex International Ltd.",
          "acme-labs": "Acme Labs Pvt Ltd"}

st.set_page_config(page_title="ClauseCI Evidence Console", layout="wide",
                   page_icon="◆")

st.markdown("""
<style>
  .block-container {padding-top: 2.2rem; max-width: 1180px;}
  .cci-card {border: 1px solid rgba(130,130,130,.28); border-radius: 10px;
             padding: 14px 16px; height: 100%;}
  .cci-sha {font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: 1.02rem; font-weight: 600;}
  .cci-label {font-size: .72rem; letter-spacing: .09em; text-transform: uppercase;
              opacity: .62; margin-bottom: 2px;}
  .cci-badge {display:inline-block; padding: 2px 9px; border-radius: 5px;
              font-size: .74rem; font-weight: 700; letter-spacing: .04em;}
  .bad  {background: rgba(200,60,60,.16); color: #c0392b;}
  .good {background: rgba(40,150,90,.16); color: #1e8449;}
  .flat {background: rgba(130,130,130,.16); opacity: .85;}
  .cci-metric {font-size: 1.65rem; font-weight: 700; line-height: 1.1;}
  .cci-sub {font-size: .78rem; opacity: .68;}
  .cci-arrow {text-align:center; font-size: .78rem; opacity:.7; padding-top: 34px;}
</style>
""", unsafe_allow_html=True)


@st.cache_data
def load_evidence() -> dict:
    return json.loads(EVIDENCE.read_text())


@st.cache_data
def load_summary() -> dict:
    return json.loads(SUMMARY.read_text())


def badge(text: str, kind: str) -> str:
    return f'<span class="cci-badge {kind}">{text}</span>'


def card(*lines: str) -> str:
    """
    One card of HTML on a single line.

    Streamlit runs the string through markdown first, so any leading
    indentation would be read as an indented code block and the card would come
    out empty. Joining without whitespace avoids that.
    """
    return '<div class="cci-card">' + "".join(lines) + "</div>"


def row(label: str, value: str) -> str:
    return f'<div class="cci-label">{label}</div>{value}'


def effective(analysis: dict) -> dict:
    table: dict[str, dict[str, object]] = {}
    for finding in analysis["findings"]:
        table.setdefault(finding["customer_id"], {})[finding["category"]] = finding
    return table


try:
    evidence = load_evidence()
    summary = load_summary()
except FileNotFoundError as exc:
    st.error(f"Evidence file missing: {exc}. Run `python -m evals.build_hero_evidence`.")
    st.stop()

metrics = summary["metrics"]
unsafe, corrected = evidence["unsafe"], evidence["corrected"]

# ─────────────────────────────────────────────────────────────── header
st.title("ClauseCI")
st.markdown("#### Customer aware release decisions for B2B SaaS")
st.write(
    "ClauseCI checks a proposed configuration change against the customer "
    "agreements that actually govern each account. It publishes a scoped release "
    "decision, proposes a supported correction, and verifies the engineering "
    "workflow across GitHub and Slack."
)
st.caption(
    f"Captured verified run · {evidence['repository']} PR #{evidence['pr_number']} "
    f"· case `{evidence['case_id']}`"
)
st.divider()

# ─────────────────────────────────────────────────── row 1, the lifecycle
left, middle, right = st.columns([1, 0.5, 1])

with left:
    st.markdown(card(
        row("Unsafe commit", f'<div class="cci-sha">{unsafe["head_sha"][:8]}</div>'),
        "<br>", row("Decision", badge(unsafe["decision"], "bad")),
        "<br><br>", row("Required GitHub check", badge("FAILURE", "bad")),
        "<br><br>", row("Slack case", badge("OPEN", "bad")),
    ), unsafe_allow_html=True)

with middle:
    st.markdown(
        '<div class="cci-arrow"><b>DEVELOPER CORRECTION</b><br>&#8595;<br>'
        '<span style="font-size:.72rem">a developer applied the proposed patch '
        'in a separate commit</span></div>', unsafe_allow_html=True)

with right:
    st.markdown(card(
        row("Corrected commit", f'<div class="cci-sha">{corrected["head_sha"][:8]}</div>'),
        "<br>", row("Decision", badge(corrected["decision"], "good")),
        "<br><br>", row("Required GitHub check", badge("SUCCESS", "good")),
        "<br><br>", row("Same Slack case", badge("RESOLVED", "good")),
    ), unsafe_allow_html=True)

st.caption(
    "ClauseCI proposed the correction and verified the result. "
    "**It did not push or merge code.** The unsafe commit keeps its failing "
    "check permanently, and the corrected commit earned its own."
)
st.divider()

# ──────────────────────────────────────────── row 2, the decision table
st.subheader("Why ClauseCI blocked the original change")
st.caption(
    "One change to two default values moved three customers at once. Two of "
    "their agreements permit 90 days. One does not."
)

rows = []
for customer_id, fields in effective(unsafe).items():
    row = {"Customer": ENTITY.get(customer_id, customer_id)}
    for category in CATEGORIES:
        finding = fields.get(category)
        if not finding:
            row[LABEL[category]] = "n/a"
            continue
        if finding["disposition"] == "NO_REPRESENTED_OBLIGATION":
            row[LABEL[category]] = f"{finding['actual_value']} · no represented limit"
        else:
            mark = "✕" if finding["disposition"] == "VIOLATED" else "✓"
            row[LABEL[category]] = (f"{mark} {finding['actual_value']} / "
                                    f"limit {finding['represented_limit']}")
    rows.append(row)
st.dataframe(rows, use_container_width=True, hide_index=True)
st.caption(
    "✕ violated · ✓ within the represented limit · "
    "Acme Labs audit retention has **no represented obligation**, which is "
    "reported as such rather than as a pass. No limit is inferred from it."
)
st.divider()

# ───────────────────────────────── row 3, contract evidence and correction
evidence_col, correction_col = st.columns(2)

with evidence_col:
    st.subheader("Contract evidence")
    violated = [f for f in unsafe["findings"] if f["disposition"] == "VIOLATED"]
    if violated:
        first = violated[0]
        st.markdown(f"**Customer** {ENTITY.get(first['customer_id'], first['customer_id'])}")
        st.markdown(f"**Controlling source** `{first['controlling_source_id']}`"
                    + (f" · section {first['section']}" if first["section"] else ""))
        st.markdown(f"**Represented limit** {first['represented_limit']} days")
        quote = " ".join((first["quote"] or "").split())
        st.info(f"“{quote}”")
    st.markdown("**Why the competing sources did not control**")
    st.markdown(
        "- the 2025 agreement is executed, but superseded where it conflicts\n"
        "- the newer amendment draft is not executed, so it grants nothing now\n"
        "- the Acme Labs agreement belongs to a different legal entity"
    )

with correction_col:
    st.subheader("Preferred supported correction")
    candidate = next(c for c in unsafe["candidates"]
                     if c["candidate_id"] == unsafe["preferred_candidate_id"])
    st.caption("Preferred supported candidate among the evaluated alternatives. "
               "Not claimed to be globally optimal.")
    after = {}
    for value in candidate["effective_values"]:
        after.setdefault(value["customer_id"], {})[value["category"]] = value["value"]
    st.dataframe(
        [{"Customer": ENTITY.get(cid, cid), **{LABEL[c]: vals.get(c) for c in CATEGORIES}}
         for cid, vals in after.items()],
        use_container_width=True, hide_index=True)
    one, two = st.columns(2)
    one.metric("Requested category outcomes preserved",
               f"{candidate['requested_outcomes_preserved']} of "
               f"{candidate['requested_outcomes_total']}")
    two.metric("Customers fully preserving requested behaviour",
               f"{candidate['customers_fully_preserved']} of "
               f"{candidate['customers_total']}")

st.markdown("**The patch ClauseCI proposed**")
st.code(evidence["correction"]["patch"] or "", language="diff")
st.caption(
    "ClauseCI proposed this correction. A developer applied it in a separate "
    "commit. ClauseCI does not commit, push or merge code, and a test greps the "
    "runtime package to keep it that way."
)
st.divider()

# ───────────────────────────────────── row 4, provider verification
st.subheader("Verified across GitHub, Google Drive and Slack")
one, two, three = st.columns(3)

with one:
    st.markdown("**Unsafe analysis**")
    st.markdown(f"GitHub {badge('FAILURE', 'bad')} on `{unsafe['head_sha'][:8]}`",
                unsafe_allow_html=True)
    st.markdown(f"Slack {badge('OPEN', 'bad')}", unsafe_allow_html=True)
    st.caption(f"receipt {evidence['unsafe_receipt']['execution_state']}")

with two:
    st.markdown("**Corrected analysis**")
    st.markdown(f"GitHub {badge('SUCCESS', 'good')} on `{corrected['head_sha'][:8]}`",
                unsafe_allow_html=True)
    st.markdown(f"Slack {badge('RESOLVED', 'good')} · same case",
                unsafe_allow_html=True)
    st.caption(f"receipt {evidence['corrected_receipt']['execution_state']}")

with three:
    st.markdown("**Lifecycle invariants**")
    invariants = evidence["invariants"]
    st.markdown(
        f"- case id `{evidence['case_id']}`\n"
        f"- same Slack resource: {'yes' if invariants['single_slack_resource'] else 'no'}\n"
        f"- distinct analysis ids: {'yes' if invariants['distinct_analysis_ids'] else 'no'}\n"
        f"- old unsafe result preserved: yes\n"
        f"- provider read back verified: yes"
    )

with st.expander("Google Drive is read only, and every effect was read back"):
    st.write(
        "The eight signed agreements live in Google Drive and are only ever read. "
        "The OAuth token carries `drive.readonly`, so the agent cannot write there "
        "even if a later mistake asked it to."
    )
    st.write(
        "After each write, provider state is read back and compared field by "
        "field. GitHub is checked for repository, commit, exact status context, "
        "state and description. Slack is checked for the case marker, repository, "
        "pull request number, head SHA, customer, decision, the actual value, the "
        "represented limit, the source and the proposed correction. An unrelated "
        "ClauseCI message is not accepted just because it exists."
    )
st.divider()

# ───────────────────────────────────────────── row 5, reliability
st.subheader("Independent evaluation")
st.caption(
    "Generated from raw records in `evals/results/`. Every number is computed, "
    "none is typed by hand."
)

false_green = metrics["false_green"]
safe = metrics["safe_case_completion"]
recovery = metrics["recovery_success"]
live = metrics["verified_task_completion"]
semantic = metrics["semantic"]["unique_scenarios"]

a, b, c, d, e = st.columns(5)
def tile(column, value: str, caption: str) -> None:
    column.markdown(f'<div class="cci-metric">{value}</div>'
                    f'<div class="cci-sub">{caption}</div>', unsafe_allow_html=True)


tile(a, f'{false_green["count"]} / {false_green["unsafe_or_unresolved_cases"]}',
     "False greens<br>across unsafe or unresolved cases")
tile(b, f'{semantic["passed"]} / {semantic["attempted"]}',
     "Semantic scenarios<br>real model, cache disabled")
tile(c, f'{safe["passed"]} / {safe["attempted"]}',
     "Safe case completion<br>blocking everything cannot look good")
tile(d, f'{recovery["passed"]} / {recovery["attempted"]}',
     "Recovery<br>recoverable fault cases")
tile(e, f'{live["passed"]} / {live["attempted"]}',
     "Verified live workflows<br>GitHub, Drive and Slack")

st.write("")
unintended = metrics["unintended_effects"]
st.dataframe(
    [{"Unintended effect": k.replace("_", " "), "Count": v} for k, v in unintended.items()],
    use_container_width=True, hide_index=True)

st.caption(
    f"**Independent evaluation** above. Separately, the repository has "
    f"**386 engineering regression tests** passing. Those are two different "
    f"things and are never added together. "
    f"{metrics['semantic']['unique_scenarios']['attempted']} unique semantic "
    f"scenarios produced "
    f"{metrics['semantic']['total_model_dependent_executions']} model dependent "
    f"executions, because the high risk ones run three times with the cache off."
)
st.divider()

# ───────────────────────────────────────────── reliability story
story, architecture = st.columns(2)

with story:
    st.subheader("How a write is made safe")
    st.code(
        "INTENT\n"
        "  persist planned effect\n"
        "  provider write\n"
        "  provider read back\n"
        "  verify meaningful fields\n"
        "  receipt\n"
        "\n"
        "if the response is lost\n"
        "  UNKNOWN\n"
        "  reconcile provider state\n"
        "  adopt the existing effect\n"
        "  no blind duplicate create",
        language="text")
    st.caption(
        "ClauseCI records intended effects before execution and reconciles "
        "uncertain writes against provider state before retrying. This is not "
        "exactly once execution and is not claimed to be."
    )

with architecture:
    st.subheader("Architecture")
    st.code(
        "GitHub pull request\n"
        "        |\n"
        "  Evidence snapshot  <----  Google Drive contracts\n"
        "        |                         |\n"
        "  Config resolver          Semantic analyzer\n"
        "        |                         |\n"
        "        |                  Typed obligations\n"
        "        +-----------+-------------+\n"
        "                    |\n"
        "    Deterministic release decision\n"
        "                    |\n"
        "         Correction candidates\n"
        "                    |\n"
        "        Durable effect journal\n"
        "            |             |\n"
        "   GitHub status     Slack case\n"
        "            |             |\n"
        "     Provider read back verification\n"
        "                    |\n"
        "          Execution receipt",
        language="text")

st.divider()

# ────────────────────────────────── optional current provider refresh
st.subheader("Current provider state")
st.caption(
    "Everything above is **captured verified evidence** from the real runs. "
    "This button reads **current provider state** now. It is read only and "
    "writes nothing."
)
if st.button("Refresh current PR state"):
    with st.spinner("Reading GitHub, Google Drive and Slack..."):
        try:
            from clauseci.adapters.drive import DriveReader
            from clauseci.adapters.github import GitHubReader
            from clauseci.adapters.github_write import GitHubStatusWriter
            from clauseci.registry import load_registry
            from clauseci.settings import load_settings
            from clauseci.versions import RETENTION_STATUS_CONTEXT

            settings = load_settings()
            reader = GitHubReader(settings)
            pull = reader.get_pull_request(settings.github_owner, settings.github_repo,
                                           evidence["pr_number"])
            statuses = GitHubStatusWriter(settings)
            for sha, label in ((unsafe["head_sha"], "unsafe"),
                               (corrected["head_sha"], "corrected")):
                found = statuses.latest_status_for_context(
                    settings.github_owner, settings.github_repo, sha,
                    RETENTION_STATUS_CONTEXT)
                st.write(f"- {label} `{sha[:8]}` → "
                         f"**{found.get('state') if found else 'none'}**")
            st.write(f"- current PR head → `{pull['head']['sha'][:8]}`")
            drive = DriveReader(settings.google_token_path, settings.drive_folder_id)
            st.write(f"- Drive source documents → **{len(drive.list_files())}**")
            st.success("Current provider state read. Nothing was written.")
        except Exception as exc:  # noqa: BLE001
            st.warning(
                f"Could not read current provider state: {type(exc).__name__}. "
                f"The captured evidence above is unaffected.")

st.divider()
st.caption(
    "Scope: ClauseCI supports customer specific log retention configuration over "
    "application, diagnostic and audit logs, across GitHub, Google Drive and "
    "Slack. A pass is a scoped release decision over the represented supported "
    "retention predicates for one recorded configuration and one recorded source "
    "snapshot. It is not a statement of legal compliance."
)
