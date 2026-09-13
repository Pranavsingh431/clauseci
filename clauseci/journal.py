"""
The durable local journal.

ClauseCI writes down what it intends to do before it does it. That is the whole
point of this file. If the process dies between the intent and the confirmation,
the intent is still on disk and the next run can go and look at the provider
instead of guessing.

No token, credential or raw provider response is stored here. Only the intended
payload, its digest, the provider resource identity, and state.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from clauseci.domain.effects import (
    EffectState,
    IllegalTransition,
    check_transition,
    is_unfinished,
)
from clauseci.settings import ROOT
from clauseci.versions import JOURNAL_SCHEMA_VERSION

DEFAULT_DB_PATH = ROOT / "runs" / "state" / "clauseci.sqlite3"
DEFAULT_LOCK_TTL_SECONDS = 120


class JournalError(RuntimeError):
    """The journal could not be used."""


class SchemaMismatch(JournalError):
    """The database was written by a different schema version."""


class LockNotAcquired(JournalError):
    """Another local execution already owns this case."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CaseRow:
    case_id: str
    namespace: str
    repository_full_name: str
    repository_id: int | None
    pr_number: int
    slack_resource_id: str | None
    slack_payload_digest: str | None
    state: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class EffectRow:
    effect_id: str
    effect_key: str
    case_id: str
    analysis_id: str
    provider: str
    action_type: str
    target: str
    intended_payload_json: str
    intended_payload_digest: str
    state: EffectState
    provider_resource_id: str | None
    attempt_count: int
    last_error: str | None
    reconciliation_state: str | None
    created_at: str
    updated_at: str


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    case_id              TEXT PRIMARY KEY,
    namespace            TEXT NOT NULL,
    repository_full_name TEXT NOT NULL,
    repository_id        INTEGER,
    pr_number            INTEGER NOT NULL,
    slack_resource_id    TEXT,
    slack_payload_digest TEXT,
    state                TEXT NOT NULL DEFAULT 'OPEN',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    analysis_id             TEXT PRIMARY KEY,
    case_id                 TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    base_sha                TEXT NOT NULL,
    head_sha                TEXT NOT NULL,
    corpus_digest           TEXT NOT NULL,
    snapshot_schema_version TEXT NOT NULL,
    parser_version          TEXT NOT NULL,
    policy_version          TEXT NOT NULL,
    semantic_model          TEXT NOT NULL,
    semantic_prompt_version TEXT NOT NULL,
    semantic_schema_version TEXT NOT NULL,
    decision_policy_version TEXT NOT NULL,
    decision                TEXT NOT NULL,
    state                   TEXT NOT NULL DEFAULT 'ANALYZED',
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS effects (
    effect_id               TEXT PRIMARY KEY,
    effect_key              TEXT NOT NULL UNIQUE,
    case_id                 TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    analysis_id             TEXT NOT NULL REFERENCES analyses(analysis_id) ON DELETE CASCADE,
    provider                TEXT NOT NULL,
    action_type             TEXT NOT NULL,
    target                  TEXT NOT NULL,
    intended_payload_json   TEXT NOT NULL,
    intended_payload_digest TEXT NOT NULL,
    state                   TEXT NOT NULL,
    provider_resource_id    TEXT,
    attempt_count           INTEGER NOT NULL DEFAULT 0,
    last_error              TEXT,
    reconciliation_state    TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS receipts (
    receipt_id      TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    analysis_id     TEXT NOT NULL,
    head_sha        TEXT NOT NULL,
    corpus_digest   TEXT NOT NULL,
    execution_state TEXT NOT NULL,
    receipt_json    TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS case_locks (
    case_id     TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    expires_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cases_identity   ON cases(repository_full_name, pr_number);
CREATE INDEX IF NOT EXISTS idx_analyses_case    ON analyses(case_id, head_sha);
CREATE INDEX IF NOT EXISTS idx_effects_key      ON effects(effect_key);
CREATE INDEX IF NOT EXISTS idx_effects_open     ON effects(state, case_id);
CREATE INDEX IF NOT EXISTS idx_receipts_case    ON receipts(case_id, created_at);
"""


class Journal:
    """Durable record of intent, outcome and provider identity."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DB_PATH
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.path), timeout=30,
                                           isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        if str(self.path) != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._initialise()

    def close(self) -> None:
        self._connection.close()

    # ------------------------------------------------------------- schema

    def _initialise(self) -> None:
        self._connection.executescript(SCHEMA)
        row = self._connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(JOURNAL_SCHEMA_VERSION),),
            )
        elif int(row["value"]) != JOURNAL_SCHEMA_VERSION:
            raise SchemaMismatch(
                f"the journal at {self.path} is schema version {row['value']}, "
                f"this build expects {JOURNAL_SCHEMA_VERSION}. refusing to use it "
                f"rather than risk corrupting prior state"
            )

    @contextmanager
    def transaction(self):
        """One local transaction. Rolls back on any exception."""
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")

    # -------------------------------------------------------------- cases

    def upsert_case(
        self, *, case_id: str, namespace: str, repository_full_name: str,
        repository_id: int | None, pr_number: int,
    ) -> CaseRow:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO cases(case_id, namespace, repository_full_name,
                                  repository_id, pr_number, state, created_at, updated_at)
                VALUES(?,?,?,?,?, 'OPEN', ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    repository_id = COALESCE(excluded.repository_id, cases.repository_id),
                    updated_at = excluded.updated_at
                """,
                (case_id, namespace, repository_full_name, repository_id,
                 pr_number, _now(), _now()),
            )
        return self.get_case(case_id)

    def get_case(self, case_id: str) -> CaseRow | None:
        row = self._connection.execute(
            "SELECT * FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return CaseRow(**dict(row)) if row else None

    def set_case_slack_resource(
        self, case_id: str, resource_id: str | None, payload_digest: str | None
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE cases SET slack_resource_id = ?, slack_payload_digest = ?, "
                "updated_at = ? WHERE case_id = ?",
                (resource_id, payload_digest, _now(), case_id),
            )

    # ----------------------------------------------------------- analyses

    def record_analysis(self, *, analysis_id: str, case_id: str, **fields) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO analyses(
                    analysis_id, case_id, base_sha, head_sha, corpus_digest,
                    snapshot_schema_version, parser_version, policy_version,
                    semantic_model, semantic_prompt_version, semantic_schema_version,
                    decision_policy_version, decision, state, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'ANALYZED',?)
                """,
                (analysis_id, case_id, fields["base_sha"], fields["head_sha"],
                 fields["corpus_digest"], fields["snapshot_schema_version"],
                 fields["parser_version"], fields["policy_version"],
                 fields["semantic_model"], fields["semantic_prompt_version"],
                 fields["semantic_schema_version"], fields["decision_policy_version"],
                 fields["decision"], _now()),
            )

    def analyses_for_case(self, case_id: str) -> list[dict]:
        rows = self._connection.execute(
            "SELECT * FROM analyses WHERE case_id = ? ORDER BY created_at", (case_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------ effects

    def plan_effect(
        self, *, effect_key: str, case_id: str, analysis_id: str, provider: str,
        action_type: str, target: str, intended_payload: dict,
        intended_payload_digest: str,
    ) -> EffectRow:
        """
        Persist the intent, durably, before any provider call.

        If this key already exists the stored row is returned unchanged. That is
        what makes a repeated invocation reuse the earlier intent instead of
        inventing a second one.
        """
        existing = self.get_effect(effect_key)
        if existing is not None:
            return existing
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO effects(effect_id, effect_key, case_id, analysis_id,
                    provider, action_type, target, intended_payload_json,
                    intended_payload_digest, state, attempt_count, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?)
                """,
                (str(uuid.uuid4()), effect_key, case_id, analysis_id, provider,
                 action_type, target, json.dumps(intended_payload, sort_keys=True),
                 intended_payload_digest, EffectState.PLANNED.value, _now(), _now()),
            )
        return self.get_effect(effect_key)

    def get_effect(self, effect_key: str) -> EffectRow | None:
        row = self._connection.execute(
            "SELECT * FROM effects WHERE effect_key = ?", (effect_key,)
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["state"] = EffectState(data["state"])
        return EffectRow(**data)

    def transition_effect(
        self, effect_key: str, target: EffectState, *, outcome_known: bool = False,
        provider_resource_id: str | None = None, last_error: str | None = None,
        reconciliation_state: str | None = None, bump_attempt: bool = False,
    ) -> EffectRow:
        """Move an effect to a new state, refusing anything the machine forbids."""
        current = self.get_effect(effect_key)
        if current is None:
            raise JournalError(f"no effect with key {effect_key}")
        if current.state is not target:
            check_transition(current.state, target, outcome_known=outcome_known)
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE effects SET state = ?,
                    provider_resource_id = COALESCE(?, provider_resource_id),
                    last_error = ?,
                    reconciliation_state = COALESCE(?, reconciliation_state),
                    attempt_count = attempt_count + ?,
                    updated_at = ?
                WHERE effect_key = ?
                """,
                (target.value, provider_resource_id, last_error, reconciliation_state,
                 1 if bump_attempt else 0, _now(), effect_key),
            )
        return self.get_effect(effect_key)

    def unfinished_effects(self, case_id: str | None = None) -> list[EffectRow]:
        """Effects whose real outcome is still open. Bounded by state, not history."""
        open_states = [s.value for s in EffectState if is_unfinished(s)]
        placeholders = ",".join("?" for _ in open_states)
        query = f"SELECT * FROM effects WHERE state IN ({placeholders})"
        params: list = list(open_states)
        if case_id:
            query += " AND case_id = ?"
            params.append(case_id)
        rows = self._connection.execute(query + " ORDER BY created_at", params).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["state"] = EffectState(data["state"])
            result.append(EffectRow(**data))
        return result

    def effects_for_case(self, case_id: str) -> list[EffectRow]:
        rows = self._connection.execute(
            "SELECT * FROM effects WHERE case_id = ? ORDER BY created_at", (case_id,)
        ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["state"] = EffectState(data["state"])
            result.append(EffectRow(**data))
        return result

    # ----------------------------------------------------------- receipts

    def record_receipt(self, *, case_id: str, analysis_id: str, head_sha: str,
                       corpus_digest: str, execution_state: str, receipt_json: str) -> str:
        receipt_id = str(uuid.uuid4())
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO receipts(receipt_id, case_id, analysis_id, head_sha,
                    corpus_digest, execution_state, receipt_json, created_at)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (receipt_id, case_id, analysis_id, head_sha, corpus_digest,
                 execution_state, receipt_json, _now()),
            )
        return receipt_id

    def latest_receipt(self, case_id: str) -> dict | None:
        row = self._connection.execute(
            "SELECT * FROM receipts WHERE case_id = ? ORDER BY created_at DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        return dict(row) if row else None

    # -------------------------------------------------------------- lock

    def acquire_lock(self, case_id: str, owner: str,
                     ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS) -> bool:
        """
        Take execution ownership of one case.

        A stale lock past its expiry is taken over, so a crashed process cannot
        block the case forever. This is a local lock only. It does not attempt
        distributed coordination and does not claim to.
        """
        now = time.time()
        try:
            with self.transaction() as connection:
                row = connection.execute(
                    "SELECT owner, expires_at FROM case_locks WHERE case_id = ?",
                    (case_id,),
                ).fetchone()
                if row is not None and row["expires_at"] > now and row["owner"] != owner:
                    return False
                connection.execute(
                    """
                    INSERT INTO case_locks(case_id, owner, acquired_at, expires_at)
                    VALUES(?,?,?,?)
                    ON CONFLICT(case_id) DO UPDATE SET
                        owner = excluded.owner,
                        acquired_at = excluded.acquired_at,
                        expires_at = excluded.expires_at
                    """,
                    (case_id, owner, now, now + ttl_seconds),
                )
        except sqlite3.OperationalError:
            return False
        return True

    def release_lock(self, case_id: str, owner: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM case_locks WHERE case_id = ? AND owner = ?",
                (case_id, owner),
            )

    @contextmanager
    def case_lock(self, case_id: str, owner: str | None = None,
                  ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS):
        owner = owner or f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        if not self.acquire_lock(case_id, owner, ttl_seconds):
            raise LockNotAcquired(
                f"another local execution already owns case {case_id}. "
                f"refusing to execute the same case twice at once"
            )
        try:
            yield owner
        finally:
            self.release_lock(case_id, owner)
