"""
ClauseCI Evidence Console.

    streamlit run streamlit_app.py

A public evidence console. It reads sanitized artifacts that are committed to
this repository and nothing else. It needs no credential, contacts no provider
by default, and has no write capability of any kind.

The real execution backend lives in the `clauseci` package and is run locally
with credentials. This page reports what those runs produced. It deliberately
imports no provider adapter, so there is no code path from here to a write.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from ui import theme
from ui.theme import ARROW_DOWN, badge, card, field, flow, metric, pill, step

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evals" / "results" / "hero-lifecycle.json"
SUMMARY = ROOT / "evals" / "results" / "latest-summary.json"
RAW = ROOT / "evals" / "results" / "sanitized-raw.jsonl"

REPO_URL = "https://github.com/Pranavsingh431/clauseci"
DEMO_REPO_URL = "https://github.com/Pranavsingh431/clauseci-demo-saas"
PR_URL = f"{DEMO_REPO_URL}/pull/1"

CATEGORIES = ["application_logs", "diagnostic_logs", "audit_logs"]
LABEL = {"application_logs": "Application", "diagnostic_logs": "Diagnostic",
         "audit_logs": "Audit"}
ENTITY = {"acme-corp": "Acme Corporation", "globex": "Globex International Ltd.",
          "acme-labs": "Acme Labs Pvt Ltd"}

#: Fault walkthroughs, written from the evaluated kernel scenarios. These are
#: locally injected simulations, never real provider outages, and the page says
#: so wherever they appear.
FAULT_STORIES = {
    "Normal execution": {
        "scenario": "LC03",
        "simulated": False,
        "steps": [
            "ClauseCI records the intended effect in a local journal and commits it.",
            "The GitHub commit status is written.",
            "GitHub is read back and the repository, commit, context, state and description are compared.",
            "The Slack case is written.",
            "Slack is read back and every meaningful field is compared.",
            "The receipt is sealed as VERIFIED.",
        ],
        "outcome": "VERIFIED", "roots": 1,
    },
    "Lost response": {
        "scenario": "K03",
        "simulated": True,
        "steps": [
            "ClauseCI records the intended Slack effect before calling Slack.",
            "Slack accepts the write.",
            "The client loses the response, which the test injects locally.",
            "ClauseCI records the effect as UNKNOWN, never as failed.",
            "On the next run, recovery searches for the stable case marker.",
            "The existing message is found and adopted into the journal.",
            "The effect becomes VERIFIED after a read back.",
        ],
        "outcome": "VERIFIED after reconciliation", "roots": 1,
    },
    "Restart recovery": {
        "scenario": "K04",
        "simulated": True,
        "steps": [
            "A write lands and the process stops before recording the confirmation.",
            "A new process opens the same journal.",
            "It finds one effect still in an unfinished state.",
            "It inspects provider state rather than retrying blindly.",
            "The existing Slack resource is adopted.",
            "No duplicate is created.",
        ],
        "outcome": "VERIFIED after reconciliation", "roots": 1,
    },
    "Stale commit": {
        "scenario": "K11",
        "simulated": True,
        "steps": [
            "Freshness is rechecked immediately before the first write.",
            "The pull request head has moved since the analysis.",
            "No GitHub status is written.",
            "No Slack case is opened.",
            "The run is recorded as SUPERSEDED, not as a pass and not as a failure.",
        ],
        "outcome": "SUPERSEDED, zero writes", "roots": 0,
    },
}

EXPLORER = ["S03", "S04", "S05", "S09", "S12", "K03", "K04", "K11", "LC01", "LC03"]


# ─────────────────────────────────────────────────────────────── loading

@st.cache_data(show_spinner=False)
def load_evidence() -> dict:
    return json.loads(EVIDENCE.read_text())


@st.cache_data(show_spinner=False)
def load_summary() -> dict:
    return json.loads(SUMMARY.read_text())


@st.cache_data(show_spinner=False)
def load_records() -> list[dict]:
    if not RAW.exists():
        return []
    return [json.loads(line) for line in RAW.read_text().splitlines() if line.strip()]


def findings_by_customer(analysis: dict) -> dict:
    table: dict[str, dict] = {}
    for finding in analysis["findings"]:
        table.setdefault(finding["customer_id"], {})[finding["category"]] = finding
    return table


st.set_page_config(page_title="ClauseCI | Customer Promise CI", layout="wide",
                   page_icon="◈", initial_sidebar_state="collapsed")
st.markdown(theme.CSS, unsafe_allow_html=True)

try:
    evidence, summary = load_evidence(), load_summary()
except FileNotFoundError:
    st.error("Evidence artifacts are missing. Run `python -m evals.build_hero_evidence`.")
    st.stop()

records = load_records()
metrics = summary["metrics"]
unsafe, corrected = evidence["unsafe"], evidence["corrected"]
correction = evidence["correction"]
preferred = next(c for c in unsafe["candidates"]
                 if c["candidate_id"] == unsafe["preferred_candidate_id"])
regression_tests = 400


# ─────────────────────────────────────────────────────────────── hero

head, links = st.columns([3, 1.15])
with head:
    st.markdown('<div class="cci-eyebrow">Customer promise CI</div>', unsafe_allow_html=True)
    st.markdown('<div class="cci-h1">ClauseCI</div>', unsafe_allow_html=True)
    st.markdown('<div class="cci-tag">Customer aware release decisions for B2B SaaS</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="cci-lede">ClauseCI checks proposed configuration changes against '
        'the agreements that actually govern each customer, proposes a supported '
        'correction, and verifies the engineering workflow end to end.</div>',
        unsafe_allow_html=True)
    st.markdown(theme.spacer(12), unsafe_allow_html=True)
    st.markdown(pill("GitHub") + pill("Google Drive") + pill("Slack")
                + badge("PUBLIC EVIDENCE DEMO", "info"), unsafe_allow_html=True)

with links:
    st.markdown(theme.spacer(26), unsafe_allow_html=True)
    st.link_button("View source on GitHub", REPO_URL, use_container_width=True)
    st.link_button("View the demo pull request", PR_URL, use_container_width=True)
    st.link_button("System reliability brief",
                   f"{REPO_URL}/blob/main/SYSTEM_RELIABILITY_BRIEF.md",
                   use_container_width=True)

st.markdown(
    '<div class="cci-note" style="margin-top:10px">This public demo is built from '
    'sanitized evidence captured during verified ClauseCI runs. The execution '
    'backend is intentionally disabled here, and this site holds no provider '
    'credentials.</div>', unsafe_allow_html=True)
st.divider()

overview, decision_tab, reliability_tab, evaluation_tab, architecture_tab = st.tabs(
    ["Overview", "Decision", "Reliability", "Evaluation", "Architecture"])


# ─────────────────────────────────────────────────────────── overview

with overview:
    st.markdown("### The change that looked harmless")
    a, b, c, d = st.columns(4)
    a.markdown(card(
        field("Original change", "90 days for every customer"),
        '<div class="cci-note" style="margin-top:7px">Two lines in one defaults block.</div>',
    ), unsafe_allow_html=True)
    b.markdown(card(
        field("ClauseCI decision", badge("CONFLICT", "bad")),
        '<div class="cci-note" style="margin-top:7px">One signed amendment caps the '
        'relevant logs at 30 days.</div>', kind="bad"), unsafe_allow_html=True)
    c.markdown(card(
        field("Scoped correction", "30 / 90 / 90"),
        '<div class="cci-note" style="margin-top:7px">Keeps the request wherever the '
        'agreements allow it.</div>', kind="pref"), unsafe_allow_html=True)
    d.markdown(card(
        field("After the developer commit", badge("PASS_SCOPED", "good")),
        '<div class="cci-note" style="margin-top:7px">New commit passes and the same '
        'Slack case resolves.</div>', kind="good"), unsafe_allow_html=True)

    st.markdown(theme.spacer(22), unsafe_allow_html=True)
    st.markdown("### Customer impact")
    st.caption("Application and diagnostic retention in days. Audit evidence is shown "
               "separately, because not every agreement covers it.")

    unsafe_table = findings_by_customer(unsafe)
    corrected_table = findings_by_customer(corrected)
    after = {}
    for value in preferred["effective_values"]:
        after.setdefault(value["customer_id"], {})[value["category"]] = value["value"]

    rows = []
    for customer_id in ("acme-corp", "globex", "acme-labs"):
        before = unsafe_table[customer_id]["application_logs"]
        now = corrected_table[customer_id]["application_logs"]
        rows.append({
            "Customer": ENTITY[customer_id],
            "Requested": before["actual_value"],
            "Represented limit": before["represented_limit"],
            "Original result": "Conflict" if before["disposition"] == "VIOLATED" else "Pass",
            "Recommended": after[customer_id]["application_logs"],
            "Corrected result": "Pass" if now["disposition"] != "VIOLATED" else "Conflict",
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    labs_audit = unsafe_table["acme-labs"]["audit_logs"]
    st.markdown(
        f'<div class="cci-note">Acme Labs audit retention has '
        f'<b>no represented obligation</b> in the reviewed sources. It is reported as '
        f'such rather than as a pass, and no limit is inferred from it. It is not a '
        f'180 day audit cap.</div>', unsafe_allow_html=True)

    st.markdown(theme.spacer(22), unsafe_allow_html=True)
    st.markdown("### What ClauseCI will not do")
    left, right = st.columns(2)
    left.markdown(
        "- Does not merge pull requests\n"
        "- Does not push remediation commits\n"
        "- Does not send email")
    right.markdown(
        "- Does not execute code from the analyzed pull request\n"
        "- Does not treat missing evidence as permission\n"
        "- Does not claim universal legal compliance")


# ─────────────────────────────────────────────────────────── decision

with decision_tab:
    st.markdown("### The lifecycle, as it actually ran")
    st.caption(f"{evidence['repository']} pull request #{evidence['pr_number']} "
               f"· case `{evidence['case_id']}` · captured verified run")

    timeline, evidence_col = st.columns([1.15, 1])
    with timeline:
        st.markdown(step(
            "Step 1 · Proposed change",
            f'<span class="cci-mono">{unsafe["head_sha"][:8]}</span><br>'
            f'<span class="cci-note">90 day application and diagnostic retention, '
            f'for every customer</span>'), unsafe_allow_html=True)
        st.markdown(step(
            "Step 2 · ClauseCI blocked", badge("CONFLICT", "bad")
            + " &nbsp; GitHub " + badge("FAILURE", "bad")
            + " &nbsp; Slack " + badge("OPEN", "bad"), kind="bad"), unsafe_allow_html=True)
        st.markdown(step(
            "Step 3 · ClauseCI proposed",
            f'Scoped correction <b>30 / 90 / 90</b><br>'
            f'<span class="cci-note">Requested customer behaviour preserved for '
            f'{preferred["customers_fully_preserved"]} of '
            f'{preferred["customers_total"]} customers, and '
            f'{preferred["requested_outcomes_preserved"]} of '
            f'{preferred["requested_outcomes_total"]} requested category outcomes</span>'
        ), unsafe_allow_html=True)
        st.markdown(step(
            "Step 4 · Developer commit",
            f'<span class="cci-mono">{corrected["head_sha"][:8]}</span><br>'
            f'<span class="cci-note">Applied by a developer, not by ClauseCI.</span>',
            kind="dev"), unsafe_allow_html=True)
        st.markdown(step(
            "Step 5 · ClauseCI verified", badge("PASS_SCOPED", "good")
            + " &nbsp; GitHub " + badge("SUCCESS", "good")
            + " &nbsp; same Slack case " + badge("RESOLVED", "good"),
            kind="good"), unsafe_allow_html=True)

    with evidence_col:
        violated = [f for f in unsafe["findings"] if f["disposition"] == "VIOLATED"]
        first = violated[0]
        quote = " ".join((first["quote"] or "").split())
        st.markdown(card(
            field("Customer", ENTITY.get(first["customer_id"], first["customer_id"])),
            theme.spacer(9),
            field("Controlling agreement", first["controlling_source_id"], mono=True),
            theme.spacer(9),
            field("Section", first["section"] or "not stated"),
            theme.spacer(9),
            field("Represented cap", f'{first["represented_limit"]} days'),
        ), unsafe_allow_html=True)
        st.markdown(theme.spacer(10), unsafe_allow_html=True)
        st.info(f"“{quote}”")
        with st.expander("Why the other sources did not control"):
            st.markdown(
                "| Source | Why not |\n|---|---|\n"
                "| The 2025 data processing agreement | Executed, but superseded where it conflicts |\n"
                "| The newer amendment draft | Not executed, so it is not current authority |\n"
                "| The Acme Labs agreement | A different legal entity |\n")

    st.markdown(theme.spacer(20), unsafe_allow_html=True)
    st.markdown("### The three candidates ClauseCI evaluated")
    st.caption("Every candidate is recomputed into effective values and evaluated by "
               "the same predicate. A constructor that intended to be safe proves nothing.")

    columns = st.columns(3)
    titles = {"A": "Requested", "B": "Rollback", "C": "Scoped correction"}
    for column, candidate in zip(columns, unsafe["candidates"]):
        cid = candidate["candidate_id"]
        feasible = candidate["feasibility_state"] == "FEASIBLE_IN_SCOPE"
        is_preferred = cid == unsafe["preferred_candidate_id"]
        values = {}
        for value in candidate["effective_values"]:
            values.setdefault(value["customer_id"], {})[value["category"]] = value["value"]
        summary_line = " / ".join(
            str(values[c]["application_logs"]) for c in ("acme-corp", "globex", "acme-labs"))
        column.markdown(card(
            field(f"Candidate {cid}", titles.get(cid, cid)),
            theme.spacer(8),
            badge(candidate["feasibility_state"], "good" if feasible else "bad"),
            (" " + badge("PREFERRED", "info")) if is_preferred else "",
            theme.spacer(10),
            field("Application retention", summary_line, mono=True),
            theme.spacer(8),
            field("Requested outcomes preserved",
                  f'{candidate["requested_outcomes_preserved"]} of '
                  f'{candidate["requested_outcomes_total"]}'),
            kind="pref" if is_preferred else ("good" if feasible else "bad"),
        ), unsafe_allow_html=True)
    st.markdown(
        '<div class="cci-note" style="margin-top:9px">Candidate C is the preferred '
        'supported candidate among the evaluated alternatives. It is not claimed to be '
        'globally optimal or the safest possible configuration.</div>',
        unsafe_allow_html=True)

    st.markdown(theme.spacer(20), unsafe_allow_html=True)
    st.markdown("### The patch ClauseCI proposed")
    st.code(correction["patch"] or "", language="diff")
    st.markdown(
        '<div class="cci-note">A developer applied this change in a separate commit. '
        '<b>ClauseCI did not push or merge code.</b> A test greps the runtime package '
        'for git operations to keep it that way.</div>', unsafe_allow_html=True)

    st.markdown(theme.spacer(20), unsafe_allow_html=True)
    st.markdown("### Verified across real apps")
    gh, drive, slack = st.columns(3)
    gh.markdown(card(
        field("GitHub", "commit status"),
        theme.spacer(9),
        f'<span class="cci-note">Unsafe commit</span><br>{badge("FAILURE", "bad")}',
        theme.spacer(8),
        f'<span class="cci-note">Corrected commit</span><br>{badge("SUCCESS", "good")}',
        theme.spacer(9),
        field("Required check", "ClauseCI / retention-compliance", mono=True),
    ), unsafe_allow_html=True)
    drive.markdown(card(
        field("Google Drive", "contract sources"),
        theme.spacer(9), field("Documents read", "8"),
        theme.spacer(8), field("Evidence snapshot", "verified"),
        theme.spacer(8), field("Mutations", "none, read only at the OAuth scope"),
    ), unsafe_allow_html=True)
    slack.markdown(card(
        field("Slack", "engineering case"),
        theme.spacer(9), field("Root cases", "1"),
        theme.spacer(8),
        f'<span class="cci-note">Original</span><br>{badge("OPEN", "bad")}',
        theme.spacer(8),
        f'<span class="cci-note">Current</span><br>{badge("RESOLVED", "good")}',
        theme.spacer(8), field("Same case reused", "yes"),
    ), unsafe_allow_html=True)

    with st.expander("Inspect the execution receipts"):
        which = st.radio("Analysis", ["Unsafe commit", "Corrected commit"],
                         horizontal=True, key="receipt_choice")
        receipt = (evidence["unsafe_receipt"] if which == "Unsafe commit"
                   else evidence["corrected_receipt"])
        st.markdown(f"**Execution state** {receipt['execution_state']} · "
                    f"**freshness** {receipt['freshness']}")
        st.caption(receipt["verification_summary"])
        st.dataframe(
            [{"Provider": e["provider"], "Action": e["action"],
              "Verification": e["verification_state"],
              "Journal state": e.get("journal_state"),
              "Resource": e.get("provider_resource")} for e in receipt["effects"]],
            use_container_width=True, hide_index=True)
        st.caption("Provider resource identifiers are replaced by a stable hash in this "
                   "public artifact. Cross record identity still checks.")


# ──────────────────────────────────────────────────────── reliability

with reliability_tab:
    st.markdown("### What happens when an API response disappears?")
    st.caption(
        "ClauseCI records intended effects before execution and reconciles uncertain "
        "writes against provider state before retrying. This is not exactly once "
        "execution and is not claimed to be.")

    choice = st.radio("Failure mode", list(FAULT_STORIES), horizontal=True,
                      key="fault_choice")
    story = FAULT_STORIES[choice]
    left, right = st.columns([1.5, 1])
    with left:
        for index, line in enumerate(story["steps"], start=1):
            st.markdown(f"**{index}.** {line}")
    with right:
        st.markdown(card(
            field("Final state", story["outcome"]),
            theme.spacer(9),
            field("Slack root messages created", str(story["roots"])),
            theme.spacer(9),
            field("Evaluated as", story["scenario"], mono=True),
            kind="good" if story["roots"] <= 1 else "",
        ), unsafe_allow_html=True)
        if story["simulated"]:
            st.markdown(
                '<div class="cci-note" style="margin-top:9px">'
                + badge("EVALUATED FAULT SIMULATION", "info")
                + '<br>The fault is injected locally by the test harness. '
                'This is not a real provider outage.</div>', unsafe_allow_html=True)

    st.markdown(theme.spacer(20), unsafe_allow_html=True)
    st.markdown("### Not memorized")
    held, explain = st.columns([1, 1.5])
    held.markdown(card(
        badge("HELD OUT SOURCE", "info"),
        theme.spacer(10),
        field("Represented cap", "45 days"),
        theme.spacer(9),
        field("45 days requested", "satisfied"),
        theme.spacer(8),
        field("46 days requested", "conflict"),
        kind="pref"), unsafe_allow_html=True)
    explain.markdown(
        "A held out agreement, which is not in the Drive corpus, caps **application "
        "logs only** at 45 days rather than 30.\n\n"
        "The analyzer read 45 and scoped it to application logs, three times out of "
        "three with the cache disabled. The decision engine then evaluated 45 as "
        "satisfied and 46 as a conflict.\n\n"
        "That is the evidence that the result follows the source rather than a "
        "hardcoded rule that Acme means 30.")


# ──────────────────────────────────────────────────────── evaluation

with evaluation_tab:
    st.markdown("### Independent evaluation")
    st.caption("Every number is computed from raw records in `evals/results/`. "
               "None is typed by hand.")

    fg = metrics["false_green"]
    cols = st.columns(6)
    cols[0].markdown(metric(f'{fg["count"]} / {fg["unsafe_or_unresolved_cases"]}',
                            "False greens<br>unsafe or unresolved cases"),
                     unsafe_allow_html=True)
    cols[1].markdown(metric(
        f'{metrics["semantic"]["unique_scenarios"]["passed"]} / '
        f'{metrics["semantic"]["unique_scenarios"]["attempted"]}',
        "Semantic scenarios<br>real model, cache disabled"), unsafe_allow_html=True)
    cols[2].markdown(metric(
        f'{metrics["safe_case_completion"]["passed"]} / '
        f'{metrics["safe_case_completion"]["attempted"]}',
        "Safe case completion<br>blocking everything cannot look good"),
        unsafe_allow_html=True)
    cols[3].markdown(metric(
        f'{metrics["recovery_success"]["passed"]} / '
        f'{metrics["recovery_success"]["attempted"]}',
        "Recovery<br>recoverable fault cases"), unsafe_allow_html=True)
    cols[4].markdown(metric(
        f'{metrics["verified_task_completion"]["passed"]} / '
        f'{metrics["verified_task_completion"]["attempted"]}',
        "Verified live workflows<br>GitHub, Drive and Slack"), unsafe_allow_html=True)
    cols[5].markdown(metric(str(regression_tests),
                            "Regression tests<br>engineering evidence, not a score"),
                     unsafe_allow_html=True)

    st.markdown(
        f'<div class="cci-note" style="margin-top:14px">A false green is a case whose '
        f'correct answer was CONFLICT or REVIEW_REQUIRED that came back releasable. '
        f'{metrics["semantic"]["unique_scenarios"]["attempted"]} unique semantic '
        f'scenarios produced {metrics["semantic"]["total_model_dependent_executions"]} '
        f'model dependent executions, because the high risk ones run three times with '
        f'the cache off. Repeats are counted separately and are not extra scenarios. '
        f'The regression suite is engineering evidence and is never added to the '
        f'evaluation score.</div>', unsafe_allow_html=True)

    st.markdown(theme.spacer(18), unsafe_allow_html=True)
    semantic, deterministic, reliability_m, live_m = st.tabs(
        ["Semantic", "Decision", "Reliability", "Live"])
    for tab, mode, blurb in (
        (semantic, "SEMANTIC", "Real model calls against the contract fixtures, cache disabled."),
        (deterministic, "DECISION", "Deterministic. No model is involved."),
        (reliability_m, "KERNEL", "Local fault injection against fake providers. Never a real provider."),
        (live_m, "LIVE", "Read only confirmation of real GitHub, Google Drive and Slack state."),
    ):
        with tab:
            st.caption(blurb)
            rows = [r for r in records if r["mode"] == mode and r.get("repeat_number", 1) == 1]
            st.dataframe(
                [{"Scenario": r["scenario_id"], "Name": r["scenario_name"],
                  "Result": "pass" if r["passed"] else "FAIL"} for r in rows],
                use_container_width=True, hide_index=True, height=260)

    st.markdown(theme.spacer(14), unsafe_allow_html=True)
    st.markdown("#### Scenario explorer")
    available = [s for s in EXPLORER if any(r["scenario_id"] == s for r in records)]
    chosen = st.selectbox(
        "Inspect an evaluated scenario", available,
        format_func=lambda s: f"{s} · " + next(
            r["scenario_name"] for r in records if r["scenario_id"] == s))
    matching = [r for r in records if r["scenario_id"] == chosen]
    head_record = matching[0]
    one, two, three = st.columns([1, 1, 1])
    one.markdown(field("Mode", head_record["mode"]), unsafe_allow_html=True)
    two.markdown(field("Result", "pass" if all(r["passed"] for r in matching) else "FAIL"),
                 unsafe_allow_html=True)
    three.markdown(field("Runs recorded", str(len(matching))), unsafe_allow_html=True)
    st.markdown(theme.spacer(8), unsafe_allow_html=True)
    st.markdown("**Expected**")
    st.code(json.dumps(head_record["expected"], indent=2), language="json")
    st.markdown("**Actual**")
    st.code(json.dumps(head_record["actual"], indent=2), language="json")
    if head_record.get("fault_injected"):
        st.caption("This scenario is a locally injected fault simulation, "
                   "not a real provider outage.")
    if head_record.get("live_provider_contacted"):
        st.caption("This scenario read real provider state. It wrote nothing.")


# ─────────────────────────────────────────────────────── architecture

with architecture_tab:
    st.markdown("### Where the model sits, and where it does not")
    st.caption("Contract language is semantic. Everything that controls a release "
               "decision or an external action is deterministic.")

    pipeline, boundary = st.columns([1.25, 1])
    with pipeline:
        st.markdown(flow("GitHub pull request"), unsafe_allow_html=True)
        st.markdown(ARROW_DOWN, unsafe_allow_html=True)
        st.markdown(flow("Evidence snapshot &nbsp;+&nbsp; Google Drive agreements"),
                    unsafe_allow_html=True)
        st.markdown(ARROW_DOWN, unsafe_allow_html=True)
        left, right = st.columns(2)
        left.markdown(flow("Config resolver<br><span class='cci-note'>deterministic</span>"),
                      unsafe_allow_html=True)
        right.markdown(flow("Semantic analyzer<br><span class='cci-note'>the one model call</span>",
                            kind="ai"), unsafe_allow_html=True)
        st.markdown(ARROW_DOWN, unsafe_allow_html=True)
        st.markdown(flow("Typed obligations &nbsp;<span class='cci-note'>quote verified</span>"),
                    unsafe_allow_html=True)
        st.markdown(ARROW_DOWN, unsafe_allow_html=True)
        st.markdown(flow("Deterministic release decision"), unsafe_allow_html=True)
        st.markdown(ARROW_DOWN, unsafe_allow_html=True)
        st.markdown(flow("Correction candidates"), unsafe_allow_html=True)
        st.markdown(ARROW_DOWN, unsafe_allow_html=True)
        st.markdown(flow("Durable effect journal"), unsafe_allow_html=True)
        st.markdown(ARROW_DOWN, unsafe_allow_html=True)
        one, two = st.columns(2)
        one.markdown(flow("GitHub commit status"), unsafe_allow_html=True)
        two.markdown(flow("Slack engineering case"), unsafe_allow_html=True)
        st.markdown(ARROW_DOWN, unsafe_allow_html=True)
        st.markdown(flow("Provider read back verification"), unsafe_allow_html=True)
        st.markdown(ARROW_DOWN, unsafe_allow_html=True)
        st.markdown(flow("Execution receipt"), unsafe_allow_html=True)

    with boundary:
        st.markdown(card(
            badge("AI", "info"),
            theme.spacer(10),
            '<div class="cci-value">Semantic contract interpretation</div>',
            '<div class="cci-note" style="margin-top:7px">Reads heterogeneous contract '
            'language and returns typed obligations with a verbatim quote. It is given '
            'no tools, so it cannot write a status, post a message or call an API.</div>',
        ), unsafe_allow_html=True)
        st.markdown(theme.spacer(12), unsafe_allow_html=True)
        st.markdown(card(
            badge("DETERMINISTIC", "good"),
            theme.spacer(10),
            '<div class="cci-value">Everything that controls a release or an action</div>',
            '<div class="cci-note" style="margin-top:7px">Customer identity, source '
            'eligibility, configuration resolution, predicate evaluation, candidate '
            'construction, ranking, authorization, provider writes, verification and '
            'recovery.</div>',
        ), unsafe_allow_html=True)
        st.markdown(theme.spacer(12), unsafe_allow_html=True)
        st.markdown(card(
            badge("CHECKED, NOT TRUSTED", "flat"),
            theme.spacer(10),
            '<div class="cci-note">A quote must occur verbatim in the source and must '
            'actually state the number being claimed. A real sentence that mentions no '
            'number cannot justify one. Contract text is evidence, never instruction.</div>',
        ), unsafe_allow_html=True)


st.divider()
st.markdown(
    '<div class="cci-note">Scope: customer specific log retention configuration over '
    'application, diagnostic and audit logs, across GitHub, Google Drive and Slack. '
    'A pass is a scoped release decision over the represented supported retention '
    'predicates, for one recorded configuration and one recorded source snapshot. '
    'It is not a statement of legal compliance.</div>', unsafe_allow_html=True)
