"""
Runtime settings, read from the environment.

No secret is ever logged or placed in a snapshot. Only identifiers that are
safe to record, such as repository name and Drive folder id, are exposed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# Configuration files whose schema ClauseCI understands for retention.
SUPPORTED_RETENTION_PATHS: tuple[str, ...] = ("config/retention.yaml",)

# Paths that look retention related but whose schema is not understood. A match
# here must never be treated as safe. It is surfaced for human review.
RETENTION_RELATED_HINTS: tuple[str, ...] = (
    "retention",
    "log_retention",
    "logretention",
    "data_lifecycle",
    "purge",
    "ttl",
)


class SettingsError(RuntimeError):
    """A required setting is missing."""


@dataclass(frozen=True)
class Settings:
    github_token: str
    github_owner: str
    github_repo: str
    drive_folder_id: str
    google_credentials_path: Path
    google_token_path: Path

    @property
    def allowed_repository(self) -> str:
        return f"{self.github_owner}/{self.github_repo}"


def load_settings(env_path: Path | None = None) -> Settings:
    load_dotenv(env_path or (ROOT / ".env"))

    def need(key: str) -> str:
        value = os.environ.get(key, "").strip()
        if not value:
            raise SettingsError(f"{key} is missing or empty in the environment")
        return value

    return Settings(
        github_token=need("GITHUB_TOKEN"),
        github_owner=need("GITHUB_OWNER"),
        github_repo=need("GITHUB_REPO"),
        drive_folder_id=need("CONTRACT_DRIVE_FOLDER_ID"),
        google_credentials_path=ROOT / os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json"),
        google_token_path=ROOT / os.environ.get("GOOGLE_TOKEN_PATH", "token.json"),
    )
