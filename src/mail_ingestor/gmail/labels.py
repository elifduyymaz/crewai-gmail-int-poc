"""Resolve Gmail label names to label IDs, with an in-memory cache.

Framework-agnostic core — no CrewAI dependency. Wraps the Gmail API
``users().labels().list()`` call and caches the name->id map so repeated
resolves avoid extra API calls.
"""

from __future__ import annotations

from typing import Any


class LabelNotFoundError(RuntimeError):
    """Raised when a Gmail label name cannot be resolved to an ID."""


class LabelResolver:
    """Resolve Gmail label names to IDs, caching the full label map in memory."""

    def __init__(self, service: Any, *, user_id: str = "me") -> None:
        self._service = service
        self._user_id = user_id
        self._cache: dict[str, str] | None = None

    def resolve(self, label_name: str) -> str:
        """Return the Gmail label ID for ``label_name`` (cached)."""
        cache = self._ensure_cache()
        try:
            return cache[label_name]
        except KeyError:
            available = ", ".join(sorted(cache)) or "(none)"
            raise LabelNotFoundError(
                f"Gmail label {label_name!r} not found. Available labels: {available}."
            ) from None

    def refresh(self) -> None:
        """Discard the cached label map so the next resolve re-fetches."""
        self._cache = None

    def _ensure_cache(self) -> dict[str, str]:
        if self._cache is None:
            self._cache = self._fetch_label_map()
        return self._cache

    def _fetch_label_map(self) -> dict[str, str]:
        response = self._service.users().labels().list(userId=self._user_id).execute()
        labels = response.get("labels", [])
        return {label["name"]: label["id"] for label in labels}
