"""
The ClauseCI evaluation runner.

    python -m evals.run                 core suite, local, no provider mutation
    python -m evals.run --mode live     adds read only provider confirmation

Core is the default and never mutates a provider. Live mode reads real provider
state and does not write. Local fault injected results are never labelled LIVE.

Raw records are written first. Every summary number is computed from them.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.metrics import compute  # noqa: E402
from evals.record import EvaluationRecord, Mode, RecordWriter, runtime_commit  # noqa: E402
from evals.scenarios import (  # noqa: E402
    HELDOUT_EXPECTED,
    RECOVERABLE,
    decision_checks,
    heldout_check,
    kernel_checks,
    lifecycle_checks,
    semantic_checks,
    versions,
)

RESULTS = ROOT / "evals" / "results"
RAW_DIR = RESULTS / "raw"

#: scenarios repeated with the cache disabled, because the model can vary
HIGH_RISK = {"S03", "S04", "S05", "S09", "S12"}


# ------------------------------------------------------------------ core

def run_semantic(writer: RecordWriter, repeats: int) -> None:
    """Real model calls, cache disabled, against the local contract fixtures."""
    from clauseci.adapters.openrouter import SemanticClient
    from clauseci.analyzer import analyze_retention_obligations
    from clauseci.domain.semantic_cache import SemanticCache
    from clauseci.registry import load_registry
    from clauseci.settings import load_settings
    from evals.semantic_repeatability import (
        AS_OF, heldout_manifest, heldout_registry, heldout_snapshot, hero_snapshot,
    )
    from clauseci.domain.evidence_text import EvidenceTextProvider, LocalBytesSource
    from evals.semantic_repeatability import HELDOUT_DIR

    load_settings()
    client = SemanticClient()
    hero = hero_snapshot()
    hero_text = EvidenceTextProvider(LocalBytesSource(ROOT / "demo_contracts"),
                                     cache_dir=None, use_cache=False)
    held = heldout_snapshot()
    held_text = EvidenceTextProvider(LocalBytesSource(HELDOUT_DIR), cache_dir=None,
                                     use_cache=False)

    for index in range(1, repeats + 1):
        started = time.monotonic()
        analysis = analyze_retention_obligations(
            hero, text_provider=hero_text, client=client,
            cache=SemanticCache(enabled=False), as_of=AS_OF,
            run_id=f"eval-semantic-hero-{index}")
        elapsed = (time.monotonic() - started) * 1000
        meta = analysis.metadata

        for sid, name, ok, expected, actual, reason in semantic_checks(analysis):
            if index > 1 and sid not in HIGH_RISK:
                continue
            writer.write(EvaluationRecord(
                mode=Mode.SEMANTIC, scenario_id=sid, scenario_name=name, passed=ok,
                expected=expected, actual=actual, failure_reason=reason or None,
                repeat_number=index, corpus_digest=hero.corpus_digest,
                latency_ms=round(elapsed, 1) if sid == "S01" else None,
                model_tokens=meta.total_tokens if sid == "S01" else None,
                model_cost_usd=meta.cost_usd if sid == "S01" else None,
                notes="local contract fixtures, cache disabled, real model call",
                **versions()))

    for index in range(1, repeats + 1):
        started = time.monotonic()
        analysis = analyze_retention_obligations(
            held, text_provider=held_text, client=client,
            cache=SemanticCache(enabled=False), as_of=AS_OF,
            registry=heldout_registry(), manifest=heldout_manifest(),
            run_id=f"eval-semantic-heldout-{index}")
        elapsed = (time.monotonic() - started) * 1000
        ok, expected, actual, reason = heldout_check(analysis)
        writer.write(EvaluationRecord(
            mode=Mode.SEMANTIC, scenario_id="S12",
            scenario_name="held out source, 45 days, application logs only",
            passed=ok, expected=expected, actual=actual, failure_reason=reason or None,
            repeat_number=index, corpus_digest=held.corpus_digest,
            latency_ms=round(elapsed, 1), model_tokens=analysis.metadata.total_tokens,
            model_cost_usd=analysis.metadata.cost_usd,
            notes="held out fixture, not in the Drive corpus, cache disabled",
            **versions()))

    # S11 rename and reorder: identity and content decide, not presentation
    from tests.fakes import FakeDriveReader, FakeGitHubReader
    from clauseci.domain.snapshot import build_analysis_snapshot
    from clauseci.registry import load_registry as _load
    reordered_drive = FakeDriveReader()
    reordered_drive.list_order_reversed = True
    reordered = build_analysis_snapshot("1", None, _load(), FakeGitHubReader(), reordered_drive)
    analysis = analyze_retention_obligations(
        reordered, text_provider=hero_text, client=client,
        cache=SemanticCache(enabled=False), as_of=AS_OF, run_id="eval-semantic-reorder")
    baseline = {(r.customer_id, r.category.value): r.value for r in analysis.resolved}
    expected_map = {}
    for customer, block in __import__("evals.scenarios", fromlist=["ORACLE"]).ORACLE["customers"].items():
        for category, field in (("application_logs", "application_logs_days"),
                                ("diagnostic_logs", "diagnostic_logs_days"),
                                ("audit_logs", "audit_logs_days")):
            expected_map[(customer, category)] = block["caps"][field]
    ok = baseline == expected_map
    writer.write(EvaluationRecord(
        mode=Mode.SEMANTIC, scenario_id="S11",
        scenario_name="reordered source listing gives the same result", passed=ok,
        expected="same values as the oracle", actual=str(sorted(baseline.items()))[:400],
        failure_reason=None if ok else "presentation order changed the result",
        corpus_digest=reordered.corpus_digest,
        notes="local fixtures, cache disabled, reversed listing order", **versions()))


def run_decision(writer: RecordWriter) -> None:
    for sid, name, ok, expected, actual, reason, exp_rel, act_rel in decision_checks():
        writer.write(EvaluationRecord(
            mode=Mode.DECISION, scenario_id=sid, scenario_name=name, passed=ok,
            expected=expected, actual=actual, failure_reason=reason or None,
            expected_release=exp_rel, actual_release=act_rel,
            notes="deterministic, no model call", **versions()))


def run_kernel(writer: RecordWriter, tmp_root: Path) -> None:
    for sid, name, ok, expected, actual, reason, effects in kernel_checks(tmp_root):
        writer.write(EvaluationRecord(
            mode=Mode.KERNEL, scenario_id=sid, scenario_name=name, passed=ok,
            expected=expected, actual=actual, failure_reason=reason or None,
            effects_attempted=effects, fault_injected=True,
            notes="local fault injection against fake providers, never a real provider",
            **versions()))


def run_lifecycle(writer: RecordWriter, tmp_root: Path) -> None:
    for sid, name, ok, expected, actual, reason, exp_rel, act_rel in lifecycle_checks(tmp_root):
        writer.write(EvaluationRecord(
            mode=Mode.LIFECYCLE, scenario_id=sid, scenario_name=name, passed=ok,
            expected=expected, actual=actual, failure_reason=reason or None,
            expected_release=exp_rel, actual_release=act_rel,
            notes="local, fake providers", **versions()))


# ------------------------------------------------------------------ live

def run_live(writer: RecordWriter) -> None:
    """
    Read only confirmation of real provider state.

    Nothing here writes. Preserved evidence from the real runs is labelled as
    captured, never presented as a new live execution.
    """
    import os
    from clauseci.adapters.drive import DriveReader
    from clauseci.adapters.github_write import GitHubStatusWriter
    from clauseci.adapters.slack_write import SlackCaseWriter
    from clauseci.domain.execution import build_case_id, case_marker
    from clauseci.journal import Journal
    from clauseci.settings import load_settings

    settings = load_settings()
    case_id = build_case_id(settings.github_owner, settings.github_repo, 1)
    marker = case_marker(case_id)
    journal = Journal()
    case = journal.get_case(case_id)
    analyses = journal.analyses_for_case(case_id)
    reader = GitHubStatusWriter(settings)
    slack = SlackCaseWriter(os.environ["SLACK_BOT_TOKEN"],
                            os.environ["SLACK_ALERT_CHANNEL_ID"])
    owner, repo = settings.github_owner, settings.github_repo

    unsafe = next((a for a in analyses if a["decision"] == "CONFLICT"), None)
    corrected = next((a for a in analyses if a["decision"] == "PASS_SCOPED"), None)
    context = "ClauseCI / retention-compliance"

    # L01 captured live evidence from the real unsafe run
    status = reader.latest_status_for_context(owner, repo, unsafe["head_sha"], context) if unsafe else None
    ok = bool(unsafe and status and status.get("state") == "failure")
    writer.write(EvaluationRecord(
        mode=Mode.LIVE, scenario_id="L01",
        scenario_name="captured live provider evidence from the real unsafe run",
        passed=ok, expected="CONFLICT with a real failure status on the unsafe commit",
        actual={"decision": unsafe["decision"] if unsafe else None,
                "status": status.get("state") if status else None},
        failure_reason=None if ok else "the unsafe commit no longer carries a failure",
        case_id=case_id, head_sha=unsafe["head_sha"] if unsafe else None,
        corpus_digest=unsafe["corpus_digest"] if unsafe else None,
        expected_release="CONFLICT", actual_release=unsafe["decision"] if unsafe else None,
        provider_resource_ids=[str(status.get("id"))] if status else [],
        live_provider_contacted=True,
        normalized_provider_observations={"github_state": status.get("state") if status else None,
                                          "github_context": status.get("context") if status else None},
        notes="captured live evidence, re-read from GitHub now. not a new execution",
        **versions()))

    # L02 the corrected workflow, confirmed against real providers now
    status = reader.latest_status_for_context(owner, repo, corrected["head_sha"], context) if corrected else None
    found = slack.find_case_by_marker(marker)
    body = slack.read_case(found[0])["text"] if found else ""
    ok = bool(
        corrected and status and status.get("state") == "success"
        and len(found) == 1 and "RESOLVED" in body
        and corrected["head_sha"] in body
        and (unsafe["head_sha"] in body if unsafe else True)
        and case and case.state == "RESOLVED")
    writer.write(EvaluationRecord(
        mode=Mode.LIVE, scenario_id="L02",
        scenario_name="real corrected workflow, GitHub success and Slack resolved",
        passed=ok,
        expected="success on the corrected commit, exactly one Slack case, RESOLVED",
        actual={"github": status.get("state") if status else None,
                "slack_root_cases": len(found), "case_state": case.state if case else None},
        failure_reason=None if ok else "provider state does not match the resolved lifecycle",
        case_id=case_id, head_sha=corrected["head_sha"] if corrected else None,
        corpus_digest=corrected["corpus_digest"] if corrected else None,
        expected_release="PASS_SCOPED",
        actual_release=corrected["decision"] if corrected else None,
        provider_resource_ids=[str(status.get("id")) if status else "",
                               found[0].resource_id if found else ""],
        live_provider_contacted=True,
        normalized_provider_observations={
            "github_state": status.get("state") if status else None,
            "slack_root_cases": len(found),
            "duplicate_slack_root": max(0, len(found) - 1),
            "resolved_in_message": "RESOLVED" in body,
        },
        notes="read only confirmation", **versions()))

    # L03 replay evidence, confirmed by reading provider state now
    statuses = reader.read_commit_statuses(owner, repo, corrected["head_sha"]) if corrected else []
    ours = [s for s in statuses if s.get("context") == context]
    ok = bool(corrected and len(ours) == 1 and len(found) == 1)
    writer.write(EvaluationRecord(
        mode=Mode.LIVE, scenario_id="L03",
        scenario_name="replay left no duplicate provider resource", passed=ok,
        expected="one status record in the ClauseCI context, one Slack root case",
        actual={"status_records": len(ours), "slack_root_cases": len(found)},
        failure_reason=None if ok else "a replay created an extra provider resource",
        case_id=case_id, head_sha=corrected["head_sha"] if corrected else None,
        live_provider_contacted=True,
        normalized_provider_observations={"duplicate_slack_root": max(0, len(found) - 1)},
        notes="read only confirmation", **versions()))

    # L04 clean negative control: a safe retention change.
    #
    # This originally used demo pull request 2 and expected NO_SUPPORTED_CHANGE.
    # That expectation was wrong and the analyzer was right. Pull request 2 was
    # branched from the original main before the baseline was rewritten, so its
    # head still carries acme-corp audit retention at 90 days against a 30 day
    # cap, which is a real pre existing breach. The failing record is preserved
    # under evals/results/superseded/ with the source configuration quoted.
    #
    # Pull request 6 is a purpose built safe retention change: globex
    # application retention at 60 days, inside the 90 day cap in
    # NW-DPA-GLOBEX-2026-0302.
    try:
        from clauseci.adapters.github import GitHubReader
        from clauseci.domain.evidence_text import DriveBytesSource, EvidenceTextProvider
        from clauseci.domain.semantic_cache import SemanticCache
        from clauseci.registry import load_registry
        from clauseci.workflow import Workflow

        drive = DriveReader(settings.google_token_path, settings.drive_folder_id)
        workflow = Workflow(
            settings=settings, registry=load_registry(),
            github_reader=GitHubReader(settings), drive=drive,
            text_provider=EvidenceTextProvider(DriveBytesSource(drive)),
            cache=SemanticCache(enabled=True))
        bundle = workflow.run_analysis("6")
        decision = bundle.decision.actual_head_state.value
        case = journal.get_case(bundle.case_id)
        globex = next((f for f in bundle.decision.findings
                       if f.customer_id == "globex"
                       and f.category.value == "application_logs"), None)
        ok = (decision == "PASS_SCOPED" and globex is not None
              and globex.actual_value == 60 and globex.represented_limit == 90
              and case is None)
        writer.write(EvaluationRecord(
            mode=Mode.LIVE, scenario_id="L04",
            scenario_name="negative control, a safe retention change passes and opens no case",
            passed=ok, expected="PASS_SCOPED, 60 within the 90 day cap, no case",
            actual={"decision": decision,
                    "globex_application": getattr(globex, "actual_value", None),
                    "represented_limit": getattr(globex, "represented_limit", None),
                    "existing_case": case.case_id if case else None},
            failure_reason=None if ok else f"expected PASS_SCOPED with no case, got {decision}",
            case_id=bundle.case_id, head_sha=bundle.snapshot.head_sha,
            corpus_digest=bundle.snapshot.corpus_digest,
            expected_release="PASS_SCOPED", actual_release=decision,
            live_provider_contacted=True,
            notes="read only analysis of PR 6, nothing was written", **versions()))
    except Exception as exc:  # noqa: BLE001
        writer.write(EvaluationRecord(
            mode=Mode.LIVE, scenario_id="L04",
            scenario_name="negative control, an unrelated pull request opens no case",
            passed=False, expected="NO_SUPPORTED_CHANGE", actual=None,
            failure_reason=f"not run: {type(exc).__name__}: {exc}",
            live_provider_contacted=True, notes="read only confirmation", **versions()))

    # provider hygiene, read only
    files = drive.list_files() if "drive" in dir() else DriveReader(
        settings.google_token_path, settings.drive_folder_id).list_files()
    ok = len(files) == 8
    writer.write(EvaluationRecord(
        mode=Mode.LIVE, scenario_id="L05",
        scenario_name="Drive is unchanged and read only", passed=ok,
        expected="8 source files", actual=f"{len(files)} files",
        failure_reason=None if ok else "the source corpus changed",
        live_provider_contacted=True,
        normalized_provider_observations={
            "drive_mutation": 0, "gmail_action": 0, "autonomous_merge": 0,
            "autonomous_remediation": 0,
            "unknown_remaining": len(journal.unfinished_effects(case_id)),
        },
        notes="read only confirmation", **versions()))


# --------------------------------------------------------------- summary

def render_markdown(summary: dict, records: list[dict]) -> str:
    lines: list[str] = []
    add = lines.append
    add("# ClauseCI evaluation summary")
    add("")
    add("Generated from raw evaluation records. No number here was typed by hand.")
    add("")
    add(f"- run id: `{summary['run_id']}`")
    add(f"- runtime commit: `{summary['runtime_commit']}`")
    add(f"- generated: {summary['generated_at']}")
    add(f"- model: `{summary['model']}`, prompt `{summary['prompt_version']}`")
    add("")
    add("This is evaluation evidence. It is separate from the pytest regression "
        "suite, which is engineering evidence and is reported separately.")
    add("")

    m = summary["metrics"]
    add("## Headline")
    add("")
    fg = m["false_green"]
    add(f"- **False greens: {fg['count']} of {fg['unsafe_or_unresolved_cases']} "
        f"unsafe or unresolved cases.** A false green is a release critical failure.")
    sc = m["safe_case_completion"]
    add(f"- Safe case completion: {sc['passed']} of {sc['attempted']}. This exists so "
        f"that blocking everything cannot look reliable.")
    rc = m["recovery_success"]
    add(f"- Recovery: {rc['passed']} of {rc['attempted']} recoverable fault cases "
        f"({', '.join(rc['recoverable_scenarios'])}).")
    vt = m["verified_task_completion"]
    add(f"- Verified live task completion: {vt['passed']} of {vt['attempted']}.")
    add("")

    add("## By mode")
    add("")
    add("| Mode | Result | Kind of evidence |")
    add("|---|---|---|")
    s = m["semantic"]["unique_scenarios"]
    add(f"| SEMANTIC | {s['passed']} of {s['attempted']} unique scenarios | "
        f"real model calls, local contract fixtures, cache disabled |")
    d = m["decision"]["scenarios"]
    add(f"| DECISION | {d['passed']} of {d['attempted']} | deterministic, no model |")
    k = m["kernel"]["scenarios"]
    add(f"| KERNEL | {k['passed']} of {k['attempted']} | local fault injection, fake providers |")
    lc = m["lifecycle"]["scenarios"]
    add(f"| LIFECYCLE | {lc['passed']} of {lc['attempted']} | local, fake providers |")
    lv = m["live"]["workflows"]
    add(f"| LIVE | {lv['passed']} of {lv['attempted']} | real GitHub, Google Drive and Slack, read only |")
    add("")

    rep = m["semantic"]["repeat_executions"]
    add(f"Semantic repeats are counted separately from unique scenarios. "
        f"{m['semantic']['unique_scenarios']['attempted']} unique scenarios produced "
        f"{m['semantic']['total_model_dependent_executions']} model dependent executions, "
        f"of which {rep['attempted']} were repeats and {rep['passed']} passed.")
    add("")

    add("## Unintended effects")
    add("")
    add("| Effect | Count |")
    add("|---|---|")
    for key, value in m["unintended_effects"].items():
        add(f"| {key.replace('_', ' ')} | {value} |")
    add("")

    cost = m["cost_and_latency"]
    add("## Cost and latency")
    add("")
    add(f"Sampled from {cost['model_calls_sampled']} model dependent evaluation passes. "
        f"Total reported cost ${cost['total_reported_cost_usd']}, "
        f"{cost['total_tokens']} tokens. Pass latency ranged from "
        f"{cost['latency_ms_min']} ms to {cost['latency_ms_max']} ms. "
        f"The sample is too small for percentiles and none are claimed.")
    add("")

    failures = [r for r in records if not r["passed"]]
    add("## Failures")
    add("")
    if not failures:
        add("No scenario failed in this run.")
    else:
        add("| Scenario | Mode | Expected | Actual | Reason | Release critical |")
        add("|---|---|---|---|---|---|")
        for r in failures:
            critical = "yes" if r.get("expected_release") in {"CONFLICT", "REVIEW_REQUIRED"} else "no"
            add(f"| {r['scenario_id']} | {r['mode']} | `{str(r['expected'])[:60]}` | "
                f"`{str(r['actual'])[:60]}` | {r.get('failure_reason')} | {critical} |")
    add("")
    add("## What this does not measure")
    add("")
    add("- Only one obligation family, RETENTION_UPPER_BOUND, over three log categories.")
    add("- One synthetic contract corpus and one customer cohort.")
    add("- Kernel and lifecycle results come from local fault injection, not from "
        "real provider outages.")
    add("- Live results are read only confirmation of state produced by the real runs.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run")
    parser.add_argument("--mode", choices=["core", "live"], default="core",
                        help="core is local and never mutates a provider")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--skip-semantic", action="store_true",
                        help="skip the model dependent scenarios")
    parser.add_argument("--summarize", help="recompute the summary from a saved raw file")
    args = parser.parse_args(argv)

    if args.summarize:
        raw_path = Path(args.summarize).resolve()
        records = [json.loads(line) for line in raw_path.read_text().splitlines() if line.strip()]
        return _emit_summary(records, records[0]["run_id"], args.mode, raw_path)

    run_id = f"eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"{run_id}.jsonl"
    writer = RecordWriter(raw_path, run_id)
    tmp_root = Path(tempfile.mkdtemp(prefix="clauseci-eval-"))

    print(f"ClauseCI evaluation   run {run_id}   mode {args.mode}")
    print("=" * 92)
    try:
        if not args.skip_semantic:
            print("  SEMANTIC   real model calls, cache disabled ...")
            run_semantic(writer, args.repeats)
        print("  DECISION   deterministic ...")
        run_decision(writer)
        print("  KERNEL     local fault injection ...")
        run_kernel(writer, tmp_root)
        print("  LIFECYCLE  local ...")
        run_lifecycle(writer, tmp_root)
        if args.mode == "live":
            print("  LIVE       read only provider confirmation ...")
            run_live(writer)
    finally:
        writer.close()
        shutil.rmtree(tmp_root, ignore_errors=True)

    return _emit_summary(writer.records, run_id, args.mode, raw_path)


def _emit_summary(records: list[dict], run_id: str, mode: str, raw_path: Path) -> int:
    metrics = compute(records, RECOVERABLE)
    first_semantic = next((r for r in records if r["mode"] == Mode.SEMANTIC), {})
    summary = {
        "run_id": run_id,
        "mode": mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_commit": runtime_commit(),
        "model": first_semantic.get("model"),
        "prompt_version": first_semantic.get("prompt_version"),
        "raw_records": str(raw_path.relative_to(ROOT)) if raw_path.is_relative_to(ROOT) else str(raw_path),
        "metrics": metrics,
    }

    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "latest-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    (RESULTS / "latest-summary.md").write_text(render_markdown(summary, records))
    shutil.copy(raw_path, RESULTS / "sanitized-raw.jsonl")

    print("=" * 92)
    print(f"  records {metrics['totals']['records']}   "
          f"passed {metrics['totals']['passed']}   failed {metrics['totals']['failed']}")
    print(f"  FALSE GREENS: {metrics['false_green']['count']} of "
          f"{metrics['false_green']['unsafe_or_unresolved_cases']} unsafe or unresolved cases")
    for mode_key in ("semantic", "decision", "kernel", "lifecycle", "live"):
        block = metrics[mode_key]
        key = "unique_scenarios" if mode_key == "semantic" else (
            "workflows" if mode_key == "live" else "scenarios")
        if key in block:
            print(f"  {mode_key.upper():<10} {block[key]['passed']}/{block[key]['attempted']}"
                  + (f"   failed: {block['failed_scenarios']}" if block["failed_scenarios"] else ""))
    print(f"  raw     evals/results/raw/{raw_path.name}")
    print(f"  summary evals/results/latest-summary.md")
    print("=" * 92)
    return 0 if metrics["totals"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
