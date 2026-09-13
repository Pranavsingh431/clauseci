"""
Canonical hashing.

Two snapshots taken from identical source material must produce identical
digests, whatever order the provider happened to return things in.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    """JSON with sorted keys and no incidental whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_digest(value: Any) -> str:
    return sha256_text(canonical_json(value))


def corpus_digest(entries: Iterable[Mapping[str, Any]]) -> str:
    """
    Digest of an evidence corpus.

    Each entry must carry a provider identity, provider revision metadata, and
    a digest of the bytes. Entries are sorted by identity first, so the digest
    does not depend on the order the provider listed them.

    Filenames and folder modified times are deliberately not part of the input.
    Renaming a file must not change the digest, and changing bytes must.
    """
    canonical = sorted(
        (
            {
                "file_id": str(entry["file_id"]),
                "version": str(entry.get("version") or ""),
                "content_sha256": str(entry["content_sha256"]),
                "text_sha256": str(entry.get("text_sha256") or ""),
            }
            for entry in entries
        ),
        key=lambda item: item["file_id"],
    )
    return canonical_digest(canonical)
