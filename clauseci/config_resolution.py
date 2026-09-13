"""
Deterministic resolution of retention configuration.

This module answers one question: for a given customer and field, what value
does this configuration actually produce, and where did that value come from.

It contains no contractual caps, no customer specific thresholds, and no
expected results. Caps come from contract documents at analysis time. Keeping
them out of here is what makes the evaluation meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import yaml

# Retention fields this module understands. `display_name` and any other
# descriptive key is metadata, not a retention value.
RETENTION_FIELDS: tuple[str, ...] = (
    "application_logs_days",
    "diagnostic_logs_days",
    "audit_logs_days",
    "backup_retention_days",
)

# Every retention value in this schema is a whole number of days.
RETENTION_UNIT = "days"

# Categories ClauseCI judges, and the configuration field each one reads.
# `backup_retention_days` is parsed but is not a judged category.
CATEGORY_TO_FIELD: dict[str, str] = {
    "application_logs": "application_logs_days",
    "diagnostic_logs": "diagnostic_logs_days",
    "audit_logs": "audit_logs_days",
}
SUPPORTED_CATEGORIES: tuple[str, ...] = tuple(CATEGORY_TO_FIELD)


class ConfigError(ValueError):
    """The configuration document is not usable."""


class UnknownCustomerError(KeyError):
    """The requested customer is not present in the configuration."""


class UnknownFieldError(KeyError):
    """The requested field is not a known retention field."""


class ValueSource(str, Enum):
    """Where an effective value came from."""

    CUSTOMER_OVERRIDE = "customer_override"
    DEFAULT = "default"
    # The customer block names the field but sets it to null, which clears the
    # override. The default applies, but the intent was explicit, so it is
    # recorded differently from a field that was simply absent.
    DEFAULT_AFTER_EXPLICIT_NULL = "default_after_explicit_null"


@dataclass(frozen=True)
class EffectiveValue:
    """One resolved (customer, field) value, with its provenance."""

    customer_id: str
    field: str
    value: int
    source: ValueSource
    unit: str = RETENTION_UNIT
    config_path: str = ""


@dataclass(frozen=True)
class EffectiveChange:
    """A change in one resolved (customer, field) value between two configs."""

    customer_id: str
    field: str
    before: int | None
    after: int | None
    before_source: ValueSource | None
    after_source: ValueSource | None

    @property
    def is_increase(self) -> bool:
        """True when the value grew, which is the direction that adds risk."""
        return (
            self.before is not None
            and self.after is not None
            and self.after > self.before
        )


@dataclass(frozen=True)
class RetentionConfig:
    """A parsed retention configuration document."""

    defaults: Mapping[str, int]
    customers: Mapping[str, Mapping[str, Any]]

    def customer_ids(self) -> list[str]:
        return sorted(self.customers)

    def display_name(self, customer_id: str) -> str | None:
        self._require_customer(customer_id)
        value = self.customers[customer_id].get("display_name")
        return str(value) if value is not None else None

    def _require_customer(self, customer_id: str) -> None:
        if customer_id not in self.customers:
            raise UnknownCustomerError(
                f"customer {customer_id!r} is not in this configuration. "
                f"known customers: {', '.join(self.customer_ids()) or '(none)'}"
            )


def parse_retention_config(text: str) -> RetentionConfig:
    """Parse a retention configuration document from YAML text."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"configuration is not valid YAML: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise ConfigError("configuration root must be a mapping")

    defaults = raw.get("defaults")
    if not isinstance(defaults, Mapping):
        raise ConfigError("configuration must contain a 'defaults' mapping")

    customers = raw.get("customers")
    if not isinstance(customers, Mapping):
        raise ConfigError("configuration must contain a 'customers' mapping")

    clean_defaults: dict[str, int] = {}
    for field, value in defaults.items():
        if field in RETENTION_FIELDS:
            clean_defaults[field] = _require_day_count(value, f"defaults.{field}")

    clean_customers: dict[str, dict[str, Any]] = {}
    for customer_id, block in customers.items():
        if not isinstance(block, Mapping):
            raise ConfigError(f"customers.{customer_id} must be a mapping")
        entry: dict[str, Any] = {}
        for field, value in block.items():
            if field in RETENTION_FIELDS:
                # An explicit null clears the override rather than setting a
                # value. Anything else must be a whole day count.
                entry[field] = (
                    None
                    if value is None
                    else _require_day_count(value, f"customers.{customer_id}.{field}")
                )
            else:
                entry[field] = value
        clean_customers[str(customer_id)] = entry

    return RetentionConfig(defaults=clean_defaults, customers=clean_customers)


def load_retention_config(path: str | Path) -> RetentionConfig:
    """Load a retention configuration document from disk."""
    return parse_retention_config(Path(path).read_text())


def resolve_effective(
    config: RetentionConfig, customer_id: str, field: str
) -> EffectiveValue:
    """
    Resolve one (customer, field) pair.

    Precedence is the customer override first, then the default. A field that
    is present in neither place has no effective value and raises.
    """
    if field not in RETENTION_FIELDS:
        raise UnknownFieldError(
            f"{field!r} is not a retention field. known fields: "
            f"{', '.join(RETENTION_FIELDS)}"
        )
    config._require_customer(customer_id)

    block = config.customers[customer_id]
    override_is_explicit_null = field in block and block[field] is None

    if field in block and not override_is_explicit_null:
        return EffectiveValue(
            customer_id=customer_id,
            field=field,
            value=int(block[field]),
            source=ValueSource.CUSTOMER_OVERRIDE,
            config_path=f"customers.{customer_id}.{field}",
        )
    if field in config.defaults:
        return EffectiveValue(
            customer_id=customer_id,
            field=field,
            value=int(config.defaults[field]),
            source=(
                ValueSource.DEFAULT_AFTER_EXPLICIT_NULL
                if override_is_explicit_null
                else ValueSource.DEFAULT
            ),
            config_path=f"defaults.{field}",
        )
    raise ConfigError(
        f"{field!r} has no value for customer {customer_id!r}: it is absent "
        f"from both the customer block and defaults"
    )


def resolve_customer(
    config: RetentionConfig, customer_id: str
) -> dict[str, EffectiveValue]:
    """Resolve every retention field that has a value for one customer."""
    config._require_customer(customer_id)
    resolved: dict[str, EffectiveValue] = {}
    for field in RETENTION_FIELDS:
        try:
            resolved[field] = resolve_effective(config, customer_id, field)
        except ConfigError:
            continue  # field genuinely has no value anywhere
    return resolved


def resolve_all(config: RetentionConfig) -> dict[str, dict[str, EffectiveValue]]:
    """Resolve every field for every customer."""
    return {cid: resolve_customer(config, cid) for cid in config.customer_ids()}


def diff_effective(
    before: RetentionConfig, after: RetentionConfig
) -> list[EffectiveChange]:
    """
    Report every (customer, field) whose effective value differs.

    This is what makes a two line change to `defaults` visible as a change
    affecting three separate customers.
    """
    before_all = resolve_all(before)
    after_all = resolve_all(after)

    changes: list[EffectiveChange] = []
    for customer_id in sorted(set(before_all) | set(after_all)):
        b_fields = before_all.get(customer_id, {})
        a_fields = after_all.get(customer_id, {})
        for field in RETENTION_FIELDS:
            b = b_fields.get(field)
            a = a_fields.get(field)
            if b is None and a is None:
                continue
            if b is not None and a is not None and b.value == a.value:
                continue
            changes.append(
                EffectiveChange(
                    customer_id=customer_id,
                    field=field,
                    before=b.value if b else None,
                    after=a.value if a else None,
                    before_source=b.source if b else None,
                    after_source=a.source if a else None,
                )
            )
    return changes


def _require_day_count(value: Any, where: str) -> int:
    """A retention value must be a whole, non negative number of days."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where} must be a whole number of days, got {value!r}")
    if value < 0:
        raise ConfigError(f"{where} must not be negative, got {value!r}")
    return value
