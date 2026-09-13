"""
The local live operator.

Two things matter here. The public deployment must stay exactly as safe as it
was, which means the operator cannot render, cannot import a provider and
cannot need a credential unless a local shell asks for it. And the operator
must be a thin adapter, not a second implementation of the workflow.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ui import operator

ROOT = Path(__file__).resolve().parent.parent
OPERATOR = ROOT / "ui" / "operator.py"
CONSOLE = ROOT / "ui" / "console.py"
ENTRY = ROOT / "streamlit_app.py"

PROVIDER_PREFIXES = ("clauseci.adapters", "slack_sdk", "googleapiclient",
                     "google.oauth2", "google_auth_oauthlib", "openai")


def render(env: dict[str, str]) -> str:
    """Render the app in a subprocess with exactly this environment."""
    script = f"""
import sys, json
sys.path.insert(0, {str(ROOT)!r})
from streamlit.testing.v1 import AppTest
at = AppTest.from_file({str(ENTRY)!r}, default_timeout=240)
at.run()
print("RESULT" + json.dumps({{
    "exceptions": [str(e.value) for e in at.exception],
    "buttons": [b.label for b in at.button],
    "text_inputs": len(at.text_input),
    "tabs": len(at.tabs),
    "provider_modules": sorted(m for m in sys.modules
                               if m.startswith({PROVIDER_PREFIXES!r})),
}}))
"""
    done = subprocess.run([sys.executable, "-c", script], env=env,
                          capture_output=True, text=True, cwd=str(ROOT))
    assert done.returncode == 0, done.stderr[-2000:]
    line = next(l for l in done.stdout.splitlines() if l.startswith("RESULT"))
    import json
    return json.loads(line[len("RESULT"):])


BARE_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}


# ─────────────────────────────── OP01, OP07, OP08, the public deployment

@pytest.fixture(scope="module")
def public():
    return render(dict(BARE_ENV))


def test_op01_the_operator_is_absent_without_the_mode_variable(public):
    assert public["exceptions"] == []
    assert public["buttons"] == [], "the public page offers a button"
    assert public["text_inputs"] == 0, "the public page offers an input"


def test_op07_public_mode_imports_no_writer_and_no_provider(public):
    assert public["provider_modules"] == [], \
        f"the public page loaded {public['provider_modules']}"


def test_op08_public_mode_needs_no_secret_at_all(public):
    """It rendered under an environment holding nothing but PATH and HOME."""
    assert public["tabs"] >= 5 and public["exceptions"] == []


def test_op08b_the_operator_module_imports_nothing_risky_at_module_scope():
    """Every provider import must sit inside a function, not at the top."""
    tree = ast.parse(OPERATOR.read_text())
    top_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.add(node.module)
    assert not any(name.startswith("clauseci") for name in top_level), \
        f"ui/operator.py imports {top_level} at module scope"
    for name in top_level:
        assert not name.startswith(PROVIDER_PREFIXES), f"module scope imports {name}"


# ───────────────────────────────────────── OP02, OP03, OP04, the trigger

@pytest.fixture(scope="module")
def operator_mode():
    return render(dict(BARE_ENV, CLAUSECI_OPERATOR_MODE="1"))


def test_op02_the_operator_appears_when_the_mode_is_set(operator_mode):
    assert operator_mode["exceptions"] == []
    assert "Run ClauseCI" in operator_mode["buttons"]
    assert operator_mode["text_inputs"] == 1


def test_op03_rendering_the_operator_executes_nothing(operator_mode):
    """The page rendered with no credential present and did not raise.

    If rendering executed anything it would have had to build a workflow, and
    building a workflow without a token raises.
    """
    assert operator_mode["exceptions"] == []
    assert operator_mode["provider_modules"] == [], \
        f"merely rendering loaded {operator_mode['provider_modules']}"


def test_op04_execution_sits_behind_the_button():
    """In the source, nothing runs before the button is read and returned on."""
    source = CONSOLE.read_text()
    section = source[source.index("def render_operator"):]
    section = section[:section.index("\ndef findings_by_customer")]

    button = section.index("st.button")
    guard = section.index("if not triggered:")
    run_call = section.index("operator.run(")
    assert button < guard < run_call, \
        "the workflow is called before the button result is checked"


def test_op04b_a_disabled_operator_refuses_to_run():
    os.environ.pop("CLAUSECI_OPERATOR_MODE", None)
    assert not operator.enabled()
    with pytest.raises(operator.OperatorError, match="off"):
        operator.run("7")


# ──────────────────────────────── OP05, OP06, a thin adapter, not a copy

def test_op05_the_operator_calls_the_existing_workflow_entry_points():
    tree = ast.parse(OPERATOR.read_text())
    called = {ast.unparse(node.func) for node in ast.walk(tree)
              if isinstance(node, ast.Call)}
    for method in ("workflow.run_analysis", "workflow.build_plan", "workflow.execute"):
        assert method in called, f"the operator never calls {method}"
    assert "Workflow" in {ast.unparse(n.func).split("(")[0] for n in ast.walk(tree)
                          if isinstance(n, ast.Call)}


def test_op06_the_operator_reimplements_no_product_logic():
    """No decision, candidate, journal or provider write lives in this file."""
    source = OPERATOR.read_text()
    tree = ast.parse(source)
    called = {ast.unparse(node.func) for node in ast.walk(tree)
              if isinstance(node, ast.Call)}

    for owned_by_the_product in ("evaluate_findings", "decide_state", "build_candidates",
                                 "rank_candidates", "create_status", "post_message",
                                 "chat_postMessage", "requests.post"):
        assert owned_by_the_product not in called, \
            f"the operator implements {owned_by_the_product} itself"

    # and it defines no rule of its own
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            assert node.name in {"enabled", "parse_pr", "build_workflow", "run",
                                 "notify", "on_phase"}, \
                f"the operator grew a function of its own: {node.name}"


# ───────────────────────────────────── OP09 to OP12, what a viewer sees

def test_op09_the_operator_renders_no_secret():
    source = OPERATOR.read_text() + CONSOLE.read_text()
    for reading in ("os.environ.get('SLACK_BOT_TOKEN')",
                    'os.environ.get("SLACK_BOT_TOKEN")'):
        # reading a token to build the client is fine; rendering it is not
        if reading in source:
            break

    tree = ast.parse(CONSOLE.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func).startswith(
                ("st.write", "st.markdown", "st.code", "st.text")):
            rendered = ast.unparse(node)
            for secret in ("token", "TOKEN", "api_key", "secret", "channel_id",
                           "SLACK_ALERT_CHANNEL_ID"):
                assert secret not in rendered, f"a render call carries {secret}"


@pytest.mark.parametrize("bad,message", [
    ("", "Enter a pull request"),
    ("abc", "is not a pull request"),
    ("0", "starts at 1"),
    ("   ", "Enter a pull request"),
])
def test_op10_malformed_input_is_a_sentence_not_a_traceback(bad, message):
    with pytest.raises(operator.OperatorError) as raised:
        operator.parse_pr(bad)
    assert message in str(raised.value)
    assert "Traceback" not in str(raised.value)


@pytest.mark.parametrize("good", ["7", "owner/repo#7",
                                  "https://github.com/o/r/pull/7"])
def test_op10b_real_references_are_accepted(good):
    assert operator.parse_pr(good) == good


def test_op11_and_op12_the_result_carries_the_sha_and_the_receipt_state():
    """The result type must be able to show what the demo has to show."""
    fields = operator.RunResult.__dataclass_fields__
    for required in ("head_sha", "decision", "execution_state", "freshness",
                     "github_state", "github_verification", "slack_action",
                     "slack_verification", "verification_summary",
                     "preferred_candidate", "repository", "pr_number"):
        assert required in fields, f"RunResult cannot report {required}"

    source = CONSOLE.read_text()
    section = source[source.index("def render_operator"):]
    section = section[:section.index("\ndef findings_by_customer")]
    assert "result.head_sha" in section, "the result card never shows the analyzed sha"
    assert "result.execution_state" in section, "the result card never shows the receipt"
    assert "result.github_verification" in section
    assert "result.slack_verification" in section
