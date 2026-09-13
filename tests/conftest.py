import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "tests" / "fixtures"
ORACLE_PATH = ROOT / "evals" / "ground_truth" / "hero_retention.yaml"
REGISTRY_PATH = ROOT / "clauseci" / "data" / "customer_registry.yaml"


@pytest.fixture(scope="session")
def oracle() -> dict:
    """Hand written expected answers. Never produced by the analyzer."""
    return yaml.safe_load(ORACLE_PATH.read_text())


@pytest.fixture(scope="session")
def registry() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text())


@pytest.fixture(scope="session")
def baseline():
    from clauseci.config_resolution import load_retention_config
    return load_retention_config(FIXTURES / "retention_baseline.yaml")


@pytest.fixture(scope="session")
def requested():
    from clauseci.config_resolution import load_retention_config
    return load_retention_config(FIXTURES / "retention_requested.yaml")


@pytest.fixture(scope="session")
def correction():
    from clauseci.config_resolution import load_retention_config
    return load_retention_config(FIXTURES / "retention_correction.yaml")


@pytest.fixture(autouse=True, scope="session")
def _never_touch_the_real_journal(tmp_path_factory):
    """
    Point the default journal path at a throwaway directory for the whole run.

    A test that forgets to pass its own journal would otherwise write fake
    provider resource ids into the real runtime database. That happened once,
    and this makes it impossible rather than unlikely.
    """
    import clauseci.journal as journal_module

    original = journal_module.DEFAULT_DB_PATH
    journal_module.DEFAULT_DB_PATH = tmp_path_factory.mktemp("journal") / "test.sqlite3"
    yield
    journal_module.DEFAULT_DB_PATH = original
