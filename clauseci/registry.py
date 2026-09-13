"""
Loading the trusted customer registry.

The registry answers identity questions only: which stable customer does a
configuration key belong to, and which documents belong to that customer. It
holds no caps and no expected results.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent / "data" / "customer_registry.yaml"


class RegistryError(ValueError):
    """The registry document is not usable."""


@dataclass(frozen=True)
class RegisteredCustomer:
    customer_id: str
    legal_entity_name: str
    jurisdiction: str
    config_key: str
    not_affiliated_with: tuple[str, ...]
    eligible_documents: tuple[str, ...]


@dataclass(frozen=True)
class CustomerRegistry:
    registry_version: int
    customers: tuple[RegisteredCustomer, ...]

    def customer_ids(self) -> list[str]:
        return [c.customer_id for c in self.customers]

    def by_id(self, customer_id: str) -> RegisteredCustomer:
        for customer in self.customers:
            if customer.customer_id == customer_id:
                return customer
        raise RegistryError(f"customer {customer_id!r} is not in the registry")

    def by_config_key(self, config_key: str) -> RegisteredCustomer:
        matches = [c for c in self.customers if c.config_key == config_key]
        if not matches:
            raise RegistryError(f"no registry customer uses config key {config_key!r}")
        if len(matches) > 1:
            raise RegistryError(
                f"config key {config_key!r} maps to more than one customer: "
                f"{', '.join(m.customer_id for m in matches)}"
            )
        return matches[0]

    def customers_for_document(self, filename: str) -> list[str]:
        return sorted(c.customer_id for c in self.customers if filename in c.eligible_documents)

    def all_eligible_documents(self) -> list[str]:
        seen: set[str] = set()
        for customer in self.customers:
            seen.update(customer.eligible_documents)
        return sorted(seen)


def load_registry(path: Path | None = None) -> CustomerRegistry:
    raw = yaml.safe_load(Path(path or DEFAULT_REGISTRY_PATH).read_text())
    if not isinstance(raw, dict) or "customers" not in raw:
        raise RegistryError("registry must be a mapping containing 'customers'")

    customers: list[RegisteredCustomer] = []
    for entry in raw["customers"]:
        try:
            customers.append(
                RegisteredCustomer(
                    customer_id=str(entry["customer_id"]),
                    legal_entity_name=str(entry["legal_entity_name"]),
                    jurisdiction=str(entry.get("jurisdiction", "")),
                    config_key=str(entry["config_key"]),
                    not_affiliated_with=tuple(entry.get("not_affiliated_with", [])),
                    eligible_documents=tuple(entry["eligible_documents"]),
                )
            )
        except KeyError as exc:
            raise RegistryError(f"registry entry is missing {exc}") from exc

    ids = [c.customer_id for c in customers]
    if len(ids) != len(set(ids)):
        raise RegistryError("registry contains duplicate customer_id values")

    return CustomerRegistry(registry_version=int(raw.get("registry_version", 0)),
                            customers=tuple(customers))
