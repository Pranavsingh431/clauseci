#!/usr/bin/env bash
# DEMO STEP: developer "fixes" PR #1 in response to the ClauseCI escalation.
# Pushes a new commit to feature/increase-log-retention, which changes the
# head SHA -> ClauseCI must re-evaluate from scratch and must NOT reuse the
# previous verdict or duplicate its Slack / Gmail artifacts.
#
# Usage:  bash prep/apply_remediation.sh
set -euo pipefail
DEMO="${CLAUSECI_DEMO_REPO:-$HOME/Downloads/clauseci-demo-saas}"
SRC="$(cd "$(dirname "$0")" && pwd)/remediation/retention.yaml"

cd "$DEMO"
git fetch -q origin
git checkout -q feature/increase-log-retention
git reset -q --hard origin/feature/increase-log-retention
cp "$SRC" config/retention.yaml
git add config/retention.yaml
git commit -q -m "Keep acme-corp at 30 days per DPA Amendment No. 1

ClauseCI flagged that raising acme-corp to 90 days breaches
NW-DPA-ACME-2026-A1 section 2.1 (30-day cap). Leaving the platform
default at 90 and pinning acme-corp back to 30."
git push -q origin feature/increase-log-retention
git checkout -q main
echo "Remediation pushed. New head SHA:"
git rev-parse --short origin/feature/increase-log-retention
