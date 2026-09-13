"""
Tests for deterministic effective configuration resolution.

These test the resolver's mechanics. They use small inline documents so a
failure points at the resolver, not at a fixture.
"""

import pytest

from clauseci.config_resolution import (
    ConfigError,
    RETENTION_FIELDS,
    UnknownCustomerError,
    UnknownFieldError,
    ValueSource,
    diff_effective,
    parse_retention_config,
    resolve_customer,
    resolve_effective,
)

DOC = """
defaults:
  application_logs_days: 30
  diagnostic_logs_days: 30
  audit_logs_days: 30
  backup_retention_days: 7
customers:
  with_override:
    display_name: "Has an override"
    application_logs_days: 15
  without_override:
    display_name: "Inherits everything"
"""


def test_customer_override_wins_over_default():
    cfg = parse_retention_config(DOC)
    got = resolve_effective(cfg, "with_override", "application_logs_days")
    assert got.value == 15
    assert got.source is ValueSource.CUSTOMER_OVERRIDE


def test_default_is_used_when_no_override():
    cfg = parse_retention_config(DOC)
    got = resolve_effective(cfg, "without_override", "application_logs_days")
    assert got.value == 30
    assert got.source is ValueSource.DEFAULT


def test_override_on_one_field_does_not_affect_others():
    cfg = parse_retention_config(DOC)
    fields = resolve_customer(cfg, "with_override")
    assert fields["application_logs_days"].source is ValueSource.CUSTOMER_OVERRIDE
    assert fields["diagnostic_logs_days"].source is ValueSource.DEFAULT
    assert fields["diagnostic_logs_days"].value == 30


def test_display_name_is_not_a_retention_field():
    assert "display_name" not in RETENTION_FIELDS
    cfg = parse_retention_config(DOC)
    assert "display_name" not in resolve_customer(cfg, "with_override")
    assert cfg.display_name("with_override") == "Has an override"


def test_unknown_customer_raises():
    cfg = parse_retention_config(DOC)
    with pytest.raises(UnknownCustomerError):
        resolve_effective(cfg, "not_a_customer", "application_logs_days")


def test_unknown_field_raises():
    cfg = parse_retention_config(DOC)
    with pytest.raises(UnknownFieldError):
        resolve_effective(cfg, "with_override", "made_up_field")


def test_field_absent_everywhere_raises_rather_than_guessing():
    doc = """
defaults:
  application_logs_days: 30
customers:
  c: {}
"""
    cfg = parse_retention_config(doc)
    with pytest.raises(ConfigError):
        resolve_effective(cfg, "c", "audit_logs_days")


def test_field_absent_everywhere_is_omitted_not_defaulted():
    doc = """
defaults:
  application_logs_days: 30
customers:
  c: {}
"""
    cfg = parse_retention_config(doc)
    fields = resolve_customer(cfg, "c")
    assert "application_logs_days" in fields
    assert "audit_logs_days" not in fields


@pytest.mark.parametrize(
    "bad",
    ["not yaml: [", "just a string", "defaults: {}", "customers: {}"],
)
def test_malformed_documents_are_rejected(bad):
    with pytest.raises(ConfigError):
        parse_retention_config(bad)


@pytest.mark.parametrize("value", ["30", 30.5, True, -1, None])
def test_non_day_count_values_are_rejected(value):
    doc = f"""
defaults:
  application_logs_days: {value!r}
customers:
  c: {{}}
"""
    with pytest.raises(ConfigError):
        parse_retention_config(doc)


def test_diff_reports_a_default_change_for_every_inheriting_customer():
    before = parse_retention_config(DOC)
    after = parse_retention_config(DOC.replace("application_logs_days: 30", "application_logs_days: 90", 1))
    changes = diff_effective(before, after)
    changed = {(c.customer_id, c.field) for c in changes}
    # with_override pins its own value, so only the inheriting customer moves
    assert ("without_override", "application_logs_days") in changed
    assert ("with_override", "application_logs_days") not in changed


def test_diff_records_direction_and_provenance():
    before = parse_retention_config(DOC)
    after = parse_retention_config(DOC.replace("application_logs_days: 30", "application_logs_days: 90", 1))
    change = next(
        c for c in diff_effective(before, after)
        if c.customer_id == "without_override" and c.field == "application_logs_days"
    )
    assert change.before == 30
    assert change.after == 90
    assert change.is_increase is True
    assert change.after_source is ValueSource.DEFAULT


def test_identical_configs_produce_no_changes():
    cfg = parse_retention_config(DOC)
    assert diff_effective(cfg, cfg) == []
