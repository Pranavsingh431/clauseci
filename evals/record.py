"""
The evaluation record format.

Every number in any summary must come from one of these records. Nothing is
typed by hand into a report.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVALUATION_SCHEMA_VERSION = "1.0.0"

ROOT = Path(__file__).resolve().parent.parent


class Mode:
    """Modes are kept visibly separate everywhere. A local test is never LIVE."""

    SEMANTIC = "SEMANTIC"
    DECISION = "DECISION"
    KERNEL = "KERNEL"
    LIFECYCLE = "LIFECYCLE"
    LIVE = "LIVE"


def runtime_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


@dataclass
class EvaluationRecord:
    mode: str
    scenario_id: str
    scenario_name: str
    passed: bool
    expected: Any = None
    actual: Any = None
    failure_reason: str | None = None
    repeat_number: int = 1

    #: what the expected decision is, used by the false green metric
    expected_release: str | None = None
    actual_release: str | None = None

    run_id: str = ""
    case_id: str | None = None
    input_digest: str | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    corpus_digest: str | None = None

    model: str | None = None
    prompt_version: str | None = None
    semantic_schema_version: str | None = None
    decision_policy_version: str | None = None
    execution_policy_version: str | None = None
    journal_schema_version: int | None = None

    effects_attempted: list[dict] = field(default_factory=list)
    provider_resource_ids: list[str] = field(default_factory=list)
    normalized_provider_observations: dict = field(default_factory=dict)

    latency_ms: float | None = None
    model_tokens: int | None = None
    model_cost_usd: float | None = None

    #: set only when a real provider was contacted
    live_provider_contacted: bool = False
    #: set when the case was produced by local fault injection
    fault_injected: bool = False

    started_at: str = ""
    completed_at: str = ""
    notes: str | None = None
    evaluation_schema_version: str = EVALUATION_SCHEMA_VERSION
    runtime_commit: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["runtime_commit"] = data["runtime_commit"] or runtime_commit()
        return data


#: Shapes of personal provider identifiers. A GitHub status id is a public
#: artifact of a public repository and is kept. A Slack channel and message
#: timestamp identify a private workspace, so they are replaced by a short
#: stable hash that still supports cross record identity checks.
_SLACK_RESOURCE = re.compile(r"\bC0[A-Z0-9]{7,}/[0-9]{10}\.[0-9]{6}\b")
_SLACK_CHANNEL = re.compile(r"\bC0[A-Z0-9]{7,}\b")


def sanitize_provider_ids(value):
    """Replace personal provider identifiers with a stable hash, recursively."""
    if isinstance(value, str):
        for pattern in (_SLACK_RESOURCE, _SLACK_CHANNEL):
            value = pattern.sub(
                lambda m: "res-" + hashlib.sha256(m.group(0).encode()).hexdigest()[:12],
                value)
        return value
    if isinstance(value, dict):
        return {k: sanitize_provider_ids(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_provider_ids(v) for v in value]
    return value


class RecordWriter:
    """Appends raw records as JSON lines. Raw first, summaries second."""

    def __init__(self, path: Path, run_id: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.records: list[dict] = []
        self._handle = self.path.open("w")

    def write(self, record: EvaluationRecord) -> EvaluationRecord:
        record.run_id = record.run_id or self.run_id
        record.runtime_commit = record.runtime_commit or runtime_commit()
        record.started_at = record.started_at or datetime.now(timezone.utc).isoformat()
        record.completed_at = record.completed_at or datetime.now(timezone.utc).isoformat()
        payload = sanitize_provider_ids(record.to_dict())
        self._handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
        self._handle.flush()
        self.records.append(payload)
        return record

    def close(self) -> None:
        self._handle.close()
