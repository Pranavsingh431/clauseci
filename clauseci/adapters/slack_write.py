"""
Slack adapter for the engineering case.

Only what ClauseCI needs: post a case, update a case, find a case by its stable
marker, and read one back. No arbitrary workspace operation is exposed, and no
model object is ever given an instance of this class.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


class SlackWriteError(RuntimeError):
    """A Slack operation failed."""


class AmbiguousCase(SlackWriteError):
    """More than one root case carries the same marker."""


@dataclass(frozen=True)
class SlackMessageRef:
    channel_id: str
    ts: str

    @property
    def resource_id(self) -> str:
        return f"{self.channel_id}/{self.ts}"


class SlackCaseWriter:
    """One engineering case per stable marker, in one configured channel."""

    def __init__(self, token: str, channel_id: str, client: WebClient | None = None) -> None:
        if not channel_id:
            raise SlackWriteError("no Slack channel is configured")
        self._channel_id = channel_id
        self._client = client or WebClient(token=token)

    @property
    def channel_id(self) -> str:
        return self._channel_id

    def find_case_by_marker(self, marker: str, limit: int = 200) -> list[SlackMessageRef]:
        """
        Find root messages carrying this marker.

        Thread replies are excluded, so only the case itself can match. Every
        match is returned rather than the first, because more than one is an
        ambiguity the caller must handle rather than silently pick from.
        """
        try:
            response = self._client.conversations_history(
                channel=self._channel_id, limit=limit
            )
        except SlackApiError as exc:
            raise SlackWriteError(
                f"could not read the channel history: {exc.response['error']}"
            ) from exc

        found: list[SlackMessageRef] = []
        for message in response.get("messages", []):
            thread_ts = message.get("thread_ts")
            if thread_ts and thread_ts != message.get("ts"):
                continue  # a reply, not a root case
            if marker in (message.get("text") or ""):
                found.append(SlackMessageRef(self._channel_id, message["ts"]))
        return found

    def post_case(self, text: str) -> SlackMessageRef:
        try:
            response = self._client.chat_postMessage(
                channel=self._channel_id, text=text, unfurl_links=False, unfurl_media=False
            )
        except SlackApiError as exc:
            raise SlackWriteError(f"case post failed: {exc.response['error']}") from exc
        return SlackMessageRef(self._channel_id, response["ts"])

    def update_case(self, ref: SlackMessageRef, text: str) -> SlackMessageRef:
        try:
            self._client.chat_update(channel=ref.channel_id, ts=ref.ts, text=text)
        except SlackApiError as exc:
            raise SlackWriteError(f"case update failed: {exc.response['error']}") from exc
        return ref

    def read_case(self, ref: SlackMessageRef) -> dict[str, Any]:
        """Read one case back out of provider state."""
        try:
            response = self._client.conversations_history(
                channel=ref.channel_id, oldest=ref.ts, inclusive=True, limit=5
            )
        except SlackApiError as exc:
            raise SlackWriteError(
                f"case read back failed: {exc.response['error']}"
            ) from exc
        for message in response.get("messages", []):
            if message.get("ts") == ref.ts:
                return message
        raise SlackWriteError(f"case {ref.resource_id} was not found when reading back")
