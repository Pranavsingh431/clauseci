"""
Read only Google Drive adapter.

The OAuth token carries `drive.readonly`, so this adapter cannot write to Drive
even if a later mistake asked it to. There is no upload, update or delete method
here either.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"

# The token was issued with both scopes. Only the Drive one is used here.
TOKEN_SCOPES = [DRIVE_READONLY_SCOPE, GMAIL_COMPOSE_SCOPE]

FILE_FIELDS = (
    "id,name,mimeType,size,modifiedTime,version,md5Checksum,headRevisionId,trashed"
)


class DriveError(RuntimeError):
    """A Drive read failed."""


@dataclass(frozen=True)
class DriveFile:
    """Provider metadata plus the bytes, as read at one moment."""

    file_id: str
    name: str
    mime_type: str
    size_bytes: int | None
    modified_time: str | None
    version: str | None
    head_revision_id: str | None
    md5_checksum: str | None
    content: bytes


def load_drive_credentials(token_path: Path) -> Credentials:
    if not token_path.exists():
        raise DriveError(
            f"{token_path} not found. run: ./.venv/bin/python prep/generate_token.py"
        )
    credentials = Credentials.from_authorized_user_file(str(token_path), TOKEN_SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_path.write_text(credentials.to_json())
    return credentials


class DriveReader:
    """Read only access to one Drive folder."""

    def __init__(self, token_path: Path, folder_id: str) -> None:
        self._folder_id = folder_id
        self._service = build(
            "drive", "v3", credentials=load_drive_credentials(token_path),
            cache_discovery=False,
        )

    @property
    def folder_id(self) -> str:
        return self._folder_id

    def folder_metadata(self) -> dict[str, Any]:
        return self._service.files().get(fileId=self._folder_id, fields="id,name").execute()

    def list_files(self) -> list[dict[str, Any]]:
        """Every non trashed file directly inside the configured folder."""
        found: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            response = (
                self._service.files()
                .list(
                    q=f"'{self._folder_id}' in parents and trashed=false",
                    fields=f"nextPageToken, files({FILE_FIELDS})",
                    orderBy="name",
                    pageSize=100,
                    pageToken=page_token,
                )
                .execute()
            )
            found.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return found

    def download(self, metadata: dict[str, Any]) -> DriveFile:
        """Download one file's bytes and pair them with its provider metadata."""
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(
            buffer, self._service.files().get_media(fileId=metadata["id"])
        )
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buffer.seek(0)

        raw_size = metadata.get("size")
        return DriveFile(
            file_id=metadata["id"],
            name=metadata["name"],
            mime_type=metadata.get("mimeType", ""),
            size_bytes=int(raw_size) if raw_size is not None else None,
            modified_time=metadata.get("modifiedTime"),
            version=str(metadata["version"]) if metadata.get("version") else None,
            head_revision_id=metadata.get("headRevisionId"),
            md5_checksum=metadata.get("md5Checksum"),
            content=buffer.getvalue(),
        )


def extract_pdf_text(content: bytes) -> str:
    """Extract text from PDF bytes. Raises on anything that is not a PDF."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    return "\n".join((page.extract_text() or "") for page in reader.pages)
