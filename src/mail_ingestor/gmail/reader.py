"""Gmail message transport: list message IDs by label, fetch full messages.

Framework-agnostic core — no CrewAI dependency. Thin wrapper over the Gmail API
``users().messages()`` endpoints. Returns raw Gmail message resources; MIME
parsing into ``EmailMessage`` is a later task.
"""

from __future__ import annotations

from typing import Any

from googleapiclient.errors import HttpError


class GmailReaderError(RuntimeError):
    """Raised when a Gmail messages API call fails."""


class GmailReaderService:
    """List and fetch Gmail messages via an injected googleapiclient service."""

    def __init__(self, service: Any, *, user_id: str = "me") -> None:
        self._service = service
        self._user_id = user_id

    def list_message_ids(self, label_id: str, *, max_results: int | None = None) -> list[str]:
        """List message IDs under ``label_id``, paginating up to ``max_results`` (all if None)."""
        if max_results is not None and max_results <= 0:
            return []
        message_ids: list[str] = []
        page_token: str | None = None
        try:
            while True:
                request_args: dict[str, Any] = {
                    "userId": self._user_id,
                    "labelIds": [label_id],
                }
                if page_token:
                    request_args["pageToken"] = page_token
                if max_results is not None:
                    request_args["maxResults"] = max_results - len(message_ids)
                response = self._service.users().messages().list(**request_args).execute()
                for message in response.get("messages", []):
                    message_ids.append(message["id"])
                    if max_results is not None and len(message_ids) >= max_results:
                        return message_ids
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
        except HttpError as exc:
            raise GmailReaderError(
                f"Failed to list messages for label {label_id!r}: {exc}"
            ) from exc
        return message_ids

    def get_message(self, message_id: str) -> dict[str, Any]:
        """Fetch the full Gmail message resource for ``message_id``."""
        try:
            return (
                self._service.users()
                .messages()
                .get(userId=self._user_id, id=message_id, format="full")
                .execute()
            )
        except HttpError as exc:
            raise GmailReaderError(f"Failed to get message {message_id!r}: {exc}") from exc
