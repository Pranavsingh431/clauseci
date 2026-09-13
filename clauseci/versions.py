"""
Explicit version identifiers.

Every snapshot records these. If parsing or policy changes in a way that could
change a decision, bump the matching constant so old snapshots stay readable
and comparable.

Semantic analyzer and prompt versions are deliberately absent. Phase 3 owns
those.
"""

from __future__ import annotations

# Shape of the serialized AnalysisSnapshot. Bump on any field change.
SNAPSHOT_SCHEMA_VERSION = "1.0.0"

# How retention configuration is parsed and how effective values are resolved.
# Bump if precedence, unit handling or validation changes.
CONFIG_PARSER_VERSION = "1.0.0"

# The supported surface and customer cohort. Bump when the scope changes.
POLICY_VERSION = "retention-only-1.0.0"

# The exact GitHub status context the demo repository requires on its protected
# main branch. Defined once so a later phase cannot publish under a different
# spelling and silently fail to satisfy branch protection.
RETENTION_STATUS_CONTEXT = "ClauseCI / retention-compliance"

# Used by the pre build connectivity script only. Never for product decisions.
SMOKE_TEST_STATUS_CONTEXT = "ClauseCI / smoke-test"


# ---------------------------------------------------------------- Phase 3
# The one model used for semantic interpretation. Pinned, not routed.
# Validated during preparation against the supersession case.
SEMANTIC_MODEL = "anthropic/claude-sonnet-4.5"

# Wording of the extraction and resolution prompts. Bump on any wording change,
# because a cached answer from an older prompt must not be reused.
SEMANTIC_PROMPT_VERSION = "retention-extract-1.0.0"

# Shape of the structured output the model must return.
SEMANTIC_SCHEMA_VERSION = "candidate-obligation-1.0.0"

# The single obligation family this build supports.
SUPPORTED_OBLIGATION_FAMILY = "RETENTION_UPPER_BOUND"

# Deterministic settings. One request plus at most one repair attempt.
SEMANTIC_TEMPERATURE = 0.0
SEMANTIC_TIMEOUT_SECONDS = 90
SEMANTIC_MAX_REPAIR_ATTEMPTS = 1


# ---------------------------------------------------------------- Phase 4
# Shape of the serialized ReleaseDecision.
DECISION_SCHEMA_VERSION = "1.0.0"

# The ranking and candidate construction rules. Bump when the policy changes,
# because the same inputs would then produce a different preferred candidate.
DECISION_POLICY_VERSION = "scoped-retention-decision-1.0.0"


# ---------------------------------------------------------------- Phase 5
# The rules mapping a decision to external effects. Bump when the mapping
# changes, because the same decision would then produce different writes.
EXECUTION_POLICY_VERSION = "bounded-execution-1.0.0"

# Shape of the serialized ExecutionReceipt.
RECEIPT_SCHEMA_VERSION = "2.0.0"  # Phase 6 added journal and reconciliation fields

# Namespace for stable case identity. Keeps demo cases separate from anything
# else that might later share a repository.
CASE_NAMESPACE = "clauseci-demo"


# ---------------------------------------------------------------- Phase 6
# Schema of the local SQLite journal. A mismatch fails loudly rather than
# silently reading a database written by different code.
JOURNAL_SCHEMA_VERSION = 1

# How uncertain effects are reconciled against provider state.
RECONCILIATION_POLICY_VERSION = "readback-reconcile-1.0.0"
