"""
One place that turns a known failure into a readable message.

A person who mistypes a pull request URL should get one clear line, not a stack
trace. The underlying exceptions already carry good messages and never contain a
credential, so this prints the message and exits non zero.

Set CLAUSECI_DEBUG=1 to get the traceback back for debugging.
"""

from __future__ import annotations

import os
import sys
from typing import Callable

#: Exit code for a failure the user can fix, such as a bad argument or a
#: missing credential. Distinct from 1, which a command uses to say the work
#: itself did not verify.
USAGE_EXIT_CODE = 2


def _known_errors() -> tuple[type[BaseException], ...]:
    """Imported lazily so this module stays cheap and import safe."""
    from clauseci.adapters.github import (
        GitHubError, PullRequestReferenceError, RepositoryNotAllowed,
    )
    from clauseci.adapters.openrouter import SemanticError
    from clauseci.adapters.slack_write import SlackWriteError
    from clauseci.config_resolution import ConfigError
    from clauseci.journal import JournalError
    from clauseci.registry import RegistryError
    from clauseci.settings import SettingsError
    from clauseci.sources import ManifestError

    errors: list[type[BaseException]] = [
        SettingsError, PullRequestReferenceError, RepositoryNotAllowed, GitHubError,
        SlackWriteError, SemanticError, ConfigError, JournalError, RegistryError,
        ManifestError, FileNotFoundError, PermissionError,
    ]
    try:
        from clauseci.adapters.drive import DriveError
        errors.append(DriveError)
    except Exception:  # noqa: BLE001
        pass
    try:
        from clauseci.domain.evidence_text import DigestMismatch
        errors.append(DigestMismatch)
    except Exception:  # noqa: BLE001
        pass
    try:
        from clauseci.workflow import WorkflowError
        errors.append(WorkflowError)
    except Exception:  # noqa: BLE001
        pass
    return tuple(errors)


def run_cli(main: Callable[[], int]) -> int:
    """Run a command entry point, reporting known failures as one clear line."""
    debug = os.environ.get("CLAUSECI_DEBUG", "").strip() not in ("", "0", "false")
    try:
        return main()
    except KeyboardInterrupt:
        print("\ninterrupted. nothing further was written.", file=sys.stderr)
        return 130
    except _known_errors() as exc:
        if debug:
            raise
        print(f"\nerror: {exc}", file=sys.stderr)
        print("run again with CLAUSECI_DEBUG=1 to see the full traceback.",
              file=sys.stderr)
        return USAGE_EXIT_CODE
