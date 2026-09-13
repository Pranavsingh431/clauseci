"""
Phase 9 hardening: a known failure must read as one clear line.

A person who mistypes a pull request URL should not get a stack trace, and no
error message may ever contain a credential.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "bin" / "python"
SECRET = re.compile(r"xoxb-[0-9]{6,}|github_pat_[A-Za-z0-9_]{15,}|sk-or-v1-[a-f0-9]{15,}")

pytestmark = pytest.mark.skipif(not PYTHON.exists(), reason="needs the project venv")


def run(args, env=None):
    environment = dict(os.environ)
    environment.update(env or {})
    return subprocess.run([str(PYTHON), "-m", *args], capture_output=True, text=True,
                          cwd=ROOT, env=environment, timeout=180)


@pytest.mark.parametrize("reference,expected", [
    ("not-a-pull-request", "could not parse"),
    ("https://github.com/someone/else/pull/1", "not the allowlisted repository"),
])
def test_a_bad_pull_request_reference_is_one_clear_line(reference, expected):
    result = run(["clauseci.run", "--pr", reference])
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Traceback (most recent call last)" not in combined
    assert expected in combined
    assert not SECRET.search(combined), "an error message leaked a credential"


def test_debug_mode_still_shows_the_traceback():
    result = run(["clauseci.run", "--pr", "not-a-pull-request"],
                 env={"CLAUSECI_DEBUG": "1"})
    assert "Traceback (most recent call last)" in result.stdout + result.stderr


def test_missing_credentials_name_the_variable_and_nothing_else():
    from clauseci.settings import SettingsError, load_settings

    empty = ROOT / "runs" / "empty-for-tests.env"
    empty.parent.mkdir(exist_ok=True)
    empty.write_text("")
    saved = {key: os.environ.pop(key, None)
             for key in ("GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO",
                         "CONTRACT_DRIVE_FOLDER_ID")}
    try:
        with pytest.raises(SettingsError) as caught:
            load_settings(empty)
        message = str(caught.value)
        assert "GITHUB_TOKEN" in message
        assert not SECRET.search(message)
        assert "=" not in message, "an error message should name a variable, not a value"
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def test_read_only_analysis_does_not_require_slack_credentials():
    """Slack is only needed to execute. Analysis must not demand it."""
    source = (ROOT / "clauseci" / "settings.py").read_text()
    assert "SLACK_BOT_TOKEN" not in source
    assert "SLACK_ALERT_CHANNEL_ID" not in source
    run_source = (ROOT / "clauseci" / "run.py").read_text()
    slack_block = run_source.split("if args.execute:")[1].split("workflow = Workflow")[0]
    assert "SLACK_ALERT_CHANNEL_ID" in slack_block
    assert "SLACK_BOT_TOKEN" in slack_block


def test_gmail_is_not_required_by_any_entry_point():
    """
    Bans a Gmail requirement, not the word. Several commands say in their output
    that no Gmail action occurred, which is true and should stay.
    """
    for name in ("run.py", "snapshot.py", "obligations.py", "decide.py", "state.py",
                 "correction.py", "settings.py"):
        body = (ROOT / "clauseci" / name).read_text()
        for banned in ('build("gmail"', "users().drafts", "drafts().create",
                       "GMAIL_", "NOTIFY_EMAIL_TO"):
            assert banned not in body, f"{name} requires Gmail via {banned}"


@pytest.mark.parametrize("module", [
    "clauseci.run", "clauseci.snapshot", "clauseci.obligations",
    "clauseci.decide", "clauseci.state", "clauseci.correction", "evals.run",
])
def test_every_command_has_help(module):
    result = run([module, "--help"])
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()


def test_the_run_command_makes_the_write_flag_obvious():
    result = run(["clauseci.run", "--help"])
    collapsed = " ".join(result.stdout.split())
    assert "--execute" in collapsed
    assert "nothing is written" in collapsed, "help must say the default writes nothing"
