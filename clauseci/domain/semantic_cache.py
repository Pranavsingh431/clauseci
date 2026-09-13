"""
Cache for semantic replies.

Keyed by everything that can change the answer: the model, the prompt version,
the schema version, the digest of the source bytes, the customer, the obligation
family and the step. Never by filename.

Changing a document's bytes, the prompt or the model therefore misses the cache.
Renaming a document does not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clauseci.domain.digests import sha256_text
from clauseci.settings import ROOT

DEFAULT_CACHE_DIR = ROOT / "runs" / "semantic_cache"


class SemanticCache:
    """A small on disk cache. Pass enabled=False to bypass it entirely."""

    def __init__(self, directory: Path | None = None, enabled: bool = True) -> None:
        self.directory = Path(directory or DEFAULT_CACHE_DIR)
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    @staticmethod
    def build_key(
        *,
        step: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        content_digest: str,
        customer_id: str,
        obligation_family: str,
        extra: str = "",
    ) -> str:
        return sha256_text(
            "|".join(
                [
                    step, model, prompt_version, schema_version,
                    content_digest, customer_id, obligation_family, extra,
                ]
            )
        )

    def get(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        path = self.directory / f"{key}.json"
        if not path.exists():
            self.misses += 1
            return None
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            self.misses += 1
            return None
        self.hits += 1
        return payload

    def put(self, key: str, value: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{key}.json").write_text(json.dumps(value, sort_keys=True))
