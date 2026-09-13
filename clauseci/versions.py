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
