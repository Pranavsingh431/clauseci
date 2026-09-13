"""
Getting the text of an evidence document, with its binding re verified.

A snapshot records a digest of each document's bytes, not the bytes themselves.
Phase 3 needs the text, so it fetches it again and checks that the bytes still
hash to what the snapshot recorded. If they do not, the corpus moved underneath
the snapshot and that is reported rather than analyzed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from clauseci.adapters.drive import extract_pdf_text
from clauseci.domain.digests import sha256_bytes, sha256_text
from clauseci.domain.models import DriveSourceSnapshot
from clauseci.settings import ROOT

DEFAULT_TEXT_CACHE = ROOT / "runs" / "evidence_text"


class DigestMismatch(RuntimeError):
    """The document no longer matches the digest recorded in the snapshot."""


class BytesSource(Protocol):
    """Anything that can return the bytes of a snapshot source."""

    def read_bytes(self, source: DriveSourceSnapshot) -> bytes: ...


class DriveBytesSource:
    """Fetches bytes from Drive. Read only."""

    def __init__(self, drive) -> None:  # noqa: ANN001 - DriveReader or a fake
        self._drive = drive
        self._by_id: dict[str, dict] | None = None

    def read_bytes(self, source: DriveSourceSnapshot) -> bytes:
        if self._by_id is None:
            self._by_id = {entry["id"]: entry for entry in self._drive.list_files()}
        metadata = self._by_id.get(source.file_id)
        if metadata is None:
            raise DigestMismatch(
                f"{source.name} (file id {source.file_id}) is no longer in the folder"
            )
        return self._drive.download(metadata).content


class LocalBytesSource:
    """Reads bytes from a local directory, matched by document name."""

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)

    def read_bytes(self, source: DriveSourceSnapshot) -> bytes:
        path = self._directory / source.name
        if not path.exists():
            raise DigestMismatch(f"{source.name} is not present in {self._directory}")
        return path.read_bytes()


class EvidenceTextProvider:
    """
    Returns the extracted text of a source, verifying it against the snapshot.

    Text is cached on disk by content digest, so the same bytes are only parsed
    once. The cache cannot serve the wrong document, because the digest is the
    key and it is checked against the snapshot before use.
    """

    def __init__(
        self,
        bytes_source: BytesSource,
        cache_dir: Path | None = DEFAULT_TEXT_CACHE,
        use_cache: bool = True,
    ) -> None:
        self._bytes_source = bytes_source
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._use_cache = use_cache and cache_dir is not None

    def text_for(self, source: DriveSourceSnapshot) -> str:
        if self._use_cache:
            cached = self._cache_path(source.content_sha256)
            if cached.exists():
                text = cached.read_text()
                if source.text_sha256 is None or sha256_text(text) == source.text_sha256:
                    return text

        raw = self._bytes_source.read_bytes(source)
        actual = sha256_bytes(raw)
        if actual != source.content_sha256:
            raise DigestMismatch(
                f"{source.name} has changed since the snapshot was taken. "
                f"snapshot recorded {source.content_sha256[:12]}, "
                f"the document now hashes to {actual[:12]}"
            )

        text = extract_pdf_text(raw)
        if source.text_sha256 is not None and sha256_text(text) != source.text_sha256:
            raise DigestMismatch(
                f"{source.name} bytes match but its extracted text does not. "
                f"the parser behaved differently than when the snapshot was taken"
            )

        if self._use_cache:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(source.content_sha256).write_text(text)
        return text

    def _cache_path(self, digest: str) -> Path:
        return self._cache_dir / f"{digest}.txt"
