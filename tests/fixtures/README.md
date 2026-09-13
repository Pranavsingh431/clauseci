# Test fixtures

Snapshots of configuration states used by the tests. These are inputs under
test, not expected answers. The expected answers live in
`evals/ground_truth/`, which nothing under `clauseci/` may read.

| File | Source |
|---|---|
| `retention_baseline.yaml` | `config/retention.yaml` on `main` of the demo repo |
| `retention_requested.yaml` | `config/retention.yaml` on `feature/increase-log-retention` |
| `retention_correction.yaml` | copy of `prep/remediation/retention.yaml` |

The demo repository is a separate repository, so its states are snapshotted
here to keep the tests offline and deterministic. `test_fixtures_match_source`
fails if the correction snapshot drifts from `prep/remediation/retention.yaml`.
