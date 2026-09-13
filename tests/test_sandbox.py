"""
The public release sandbox.

The sandbox is presentation. Its whole point is that it does not own a decision
rule: it rebuilds the inputs ClauseCI's engine takes and calls that engine. So
these tests care about two things. That the answers are right, and that the
answers come from the captured evidence and the real engine rather than from
numbers typed into the user interface.
"""

from __future__ import annotations

import ast
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ui import sandbox

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "ui" / "sandbox.py"
EVIDENCE = ROOT / "evals" / "results" / "hero-lifecycle.json"


@pytest.fixture(scope="module")
def evidence() -> dict:
    return json.loads(EVIDENCE.read_text())


def results(evidence: dict, days: int) -> dict[str, str]:
    """Application log disposition per customer at one requested value."""
    outcome = sandbox.evaluate(evidence, {"application_logs": days,
                                          "diagnostic_logs": days})
    return {row.customer_id: row.disposition for row in outcome.rows
            if row.category == "application_logs"}, outcome


# ───────────────────────────────────────────── SB01 to SB03, the boundary

def test_sb01_the_sandbox_runs_with_no_environment_at_all():
    """No credential, no variable, no dotenv. If it needs one, this fails."""
    script = (
        "import json, sys;"
        "sys.path.insert(0, %r);"
        "from ui.sandbox import evaluate;"
        "ev = json.load(open(%r));"
        "r = evaluate(ev, {'application_logs': 90, 'diagnostic_logs': 90});"
        "print(r.state.value)"
    ) % (str(ROOT), str(EVIDENCE))
    done = subprocess.run([sys.executable, "-c", script], env={"PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True, cwd=str(ROOT))
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "CONFLICT"


def test_sb02_the_sandbox_reaches_no_provider_and_no_model():
    """Check what it imports, and what those imports drag in."""
    tree = ast.parse(SANDBOX.read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    for module in imported:
        assert not module.startswith("clauseci.adapters"), f"reaches a provider: {module}"
        for banned in ("clauseci.workflow", "clauseci.analyzer", "clauseci.settings",
                       "clauseci.journal", "clauseci.run", "slack_sdk", "googleapiclient",
                       "google", "openai", "requests", "dotenv", "httpx"):
            assert module != banned and not module.startswith(banned + "."), \
                f"reaches {banned}"

    # and transitively, once it is actually loaded
    loaded = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(ROOT)!r}); import ui.sandbox;"
         "print('\\n'.join(sorted(sys.modules)))"],
        capture_output=True, text=True, cwd=str(ROOT))
    assert loaded.returncode == 0, loaded.stderr
    for module in loaded.stdout.split():
        assert not module.startswith(("slack_sdk", "googleapiclient", "openai",
                                      "google.oauth2", "clauseci.adapters")), \
            f"loading the sandbox pulled in {module}"


def test_sb03_the_sandbox_has_no_write_or_network_path():
    """No socket, no subprocess, no file write, no model call."""
    tree = ast.parse(SANDBOX.read_text())
    called = {ast.unparse(node.func) for node in ast.walk(tree)
              if isinstance(node, ast.Call)}
    for escape in ("open", "write_text", "requests.post", "requests.get", "urlopen",
                   "socket", "subprocess.run", "Popen", "post", "create"):
        assert escape not in called, f"the sandbox calls {escape}"


# ────────────────────────────── SB04 to SB08, the answers, from Part E

@pytest.mark.parametrize("days,expected", [
    (30,  {"acme-corp": "SATISFIED", "globex": "SATISFIED", "acme-labs": "SATISFIED"}),
    (45,  {"acme-corp": "VIOLATED",  "globex": "SATISFIED", "acme-labs": "SATISFIED"}),
    (90,  {"acme-corp": "VIOLATED",  "globex": "SATISFIED", "acme-labs": "SATISFIED"}),
    (180, {"acme-corp": "VIOLATED",  "globex": "VIOLATED",  "acme-labs": "SATISFIED"}),
    (181, {"acme-corp": "VIOLATED",  "globex": "VIOLATED",  "acme-labs": "VIOLATED"}),
])
def test_sb04_each_requested_value_lands_where_the_obligations_put_it(
        evidence, days, expected):
    per_customer, outcome = results(evidence, days)
    assert per_customer == expected
    assert outcome.state.value == ("PASS_SCOPED" if days == 30 else "CONFLICT")


def test_sb05_the_hero_request_reproduces_the_recorded_correction(evidence):
    """90 days must give back exactly what the verified run produced."""
    _, outcome = results(evidence, 90)
    proposal = outcome.preferred
    assert proposal is not None

    scoped = {value.customer_id: value.value for value in proposal.effective_values
              if value.category == "application_logs"}
    assert scoped == {"acme-corp": 30, "globex": 90, "acme-labs": 90}

    recorded = next(c for c in evidence["unsafe"]["candidates"]
                    if c["candidate_id"] == proposal.candidate_id)
    assert proposal.requested_outcomes_preserved == recorded["requested_outcomes_preserved"]
    assert proposal.requested_outcomes_total == recorded["requested_outcomes_total"]
    assert proposal.customers_fully_preserved == recorded["customers_fully_preserved"]
    assert (proposal.requested_outcomes_preserved,
            proposal.requested_outcomes_total) == (4, 6)
    assert (proposal.customers_fully_preserved, proposal.customers_total) == (2, 3)


# ─────────────────── SB06 to SB07, the answers follow the evidence

def test_sb06_the_caps_come_from_the_evidence_not_from_the_interface(evidence):
    """Move a represented limit and the decision has to move with it.

    This is the test that would catch a second decision engine written into the
    page. If the caps were typed into the sandbox, raising Acme's limit would
    change nothing.
    """
    before, _ = results(evidence, 90)
    assert before["acme-corp"] == "VIOLATED"

    lifted = copy.deepcopy(evidence)
    for finding in lifted["unsafe"]["findings"]:
        if finding["customer_id"] == "acme-corp":
            finding["represented_limit"] = 120

    after, outcome = results(lifted, 90)
    assert after["acme-corp"] == "SATISFIED", \
        "the sandbox ignored the represented limit in the evidence"
    assert outcome.state.value == "PASS_SCOPED"


def test_sb07_preservation_counts_are_computed_not_written_down(evidence):
    """Different requests must produce different counts."""
    counts = {}
    for days in (45, 90, 180, 181):
        _, outcome = results(evidence, days)
        proposal = outcome.preferred
        counts[days] = (proposal.requested_outcomes_preserved,
                        proposal.requested_outcomes_total)

    assert counts[90] == (4, 6)
    assert counts[180] == (2, 6), "a wider request must preserve less"
    assert counts[181] == (0, 6), "a request nobody can satisfy preserves nothing"
    assert len(set(counts.values())) > 1, "the counts never move, so they are fixed"


def test_sb08_an_absent_obligation_is_never_treated_as_permission(evidence):
    """Acme Labs audit has no represented cap. It must not read as a pass."""
    outcome = sandbox.evaluate(evidence, {"application_logs": 90,
                                          "diagnostic_logs": 90})
    absent = [f for f in outcome.findings
              if f.customer_id == "acme-labs" and f.category.value == "audit_logs"]
    assert absent, "the audit finding disappeared"
    assert absent[0].disposition.value == "NO_REPRESENTED_OBLIGATION"


def test_sb09_the_sandbox_exposes_no_contract_or_customer_editing():
    """Only the two requested retention values are inputs."""
    console = (ROOT / "ui" / "console.py").read_text()
    section = console[console.index("Try the release decision yourself"):]
    section = section[:section.index("The change, and what ClauseCI proposed")]

    for widget in ("text_input", "text_area", "file_uploader", "chat_input",
                   "data_editor", "multiselect"):
        assert f"st.{widget}" not in section, f"the sandbox exposes st.{widget}"
    assert section.count("st.slider") == 2, "the sandbox exposes more than two controls"
