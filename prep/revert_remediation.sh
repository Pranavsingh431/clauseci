#!/usr/bin/env bash
# Rewinds PR #1 back to the pre-fix state so the demo can be rehearsed again.
set -euo pipefail
DEMO="${CLAUSECI_DEMO_REPO:-$HOME/Downloads/clauseci-demo-saas}"
cd "$DEMO"
git fetch -q origin
git checkout -q feature/increase-log-retention
git reset -q --hard bea0bc4b
git push -q --force-with-lease origin feature/increase-log-retention
git checkout -q main
echo "PR #1 rewound to original head bea0bc4b"
