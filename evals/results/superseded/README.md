# Superseded evaluation runs

Kept on purpose. A failed run is evidence, not something to delete.

## eval-20260913T175323Z-4e557c

L04 failed. The scenario used demo pull request 2 as a clean negative control and
expected NO_SUPPORTED_CHANGE. The analyzer returned CONFLICT.

**The analyzer was right and the expectation was wrong.**

Pull request 2 was branched from the original `main` at `cea7ac7`, before the
Phase 1 work rewrote the retention baseline. Its head at
`54095e9b73e8e5c4b74ac200af22895510e06c78` therefore still carries the old
configuration, and `config/retention.yaml` at that commit reads:

```yaml
  acme-corp:
    display_name: "Acme Corporation"
    application_logs_days: 30
    diagnostic_logs_days: 30
    audit_logs_days: 90        # <- against a represented cap of 30 days
    backup_retention_days: 7
```

`NW-DPA-ACME-2026-A1` section 2.1 caps acme-corp at thirty days for application,
diagnostic **and** audit logs. 90 days exceeds that, so CONFLICT is the correct
answer. The finding is a pre existing breach on that branch, not one the pull
request introduced.

The expectation was corrected by pointing L04 at a purpose built safe retention
pull request instead, created forward from the current `main`. The original
failing record is preserved here unchanged.
