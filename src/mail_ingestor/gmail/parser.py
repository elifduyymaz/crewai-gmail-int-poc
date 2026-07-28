"""Parse a raw Gmail message (``format="full"``) into an EmailMessage.

Framework-agnostic core — no CrewAI dependency. Handles plain text, HTML
fallback (when no text/plain part exists), and multipart payloads (recursive).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Any

from mail_ingestor.schemas import EmailMessage

_BLOCK_TAGS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}


def parse_gmail_message(raw: dict[str, Any]) -> EmailMessage:
    """Convert a raw Gmail ``format="full"`` message resource into an EmailMessage."""
    payload = raw.get("payload") or {}
    headers = _headers_map(payload.get("headers", []))
    return EmailMessage(
        message_id=raw["id"],
        thread_id=raw.get("threadId"),
        subject=headers.get("subject", ""),
        sender=headers.get("from", ""),
        recipients=_parse_addresses(headers.get("to", "")),
        labels=list(raw.get("labelIds", [])),
        received_at=_internal_date_to_utc(raw["internalDate"]),
        body_text=_extract_body_text(payload),
        snippet=raw.get("snippet"),
        attachment_filenames=_collect_attachment_filenames(payload),
    )


def _headers_map(headers: list[dict[str, Any]]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in headers if "name" in h and "value" in h}


def _parse_addresses(value: str) -> list[str]:
    if not value:
        return []
    return [addr for _name, addr in getaddresses([value]) if addr]


def _internal_date_to_utc(internal_date: str) -> datetime:
    return datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC)


def _decode_body_data(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _extract_body_text(payload: dict[str, Any]) -> str:
    plain = _collect_text(payload, "text/plain")
    if plain:
        return plain
    html = _collect_text(payload, "text/html")
    if html:
        return _html_to_text(html)
    return ""


def _collect_text(part: dict[str, Any], mime_type: str) -> str:
    out: list[str] = []
    _walk_text(part, mime_type, out)
    return "\n".join(out).strip()


def _walk_text(part: dict[str, Any], mime_type: str, out: list[str]) -> None:
    if part.get("mimeType") == mime_type and not part.get("filename"):
        data = (part.get("body") or {}).get("data")
        if data:
            out.append(_decode_body_data(data))
    for sub in part.get("parts") or []:
        _walk_text(sub, mime_type, out)


def _collect_attachment_filenames(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    _walk_attachments(payload, out)
    return out


def _walk_attachments(part: dict[str, Any], out: list[str]) -> None:
    filename = part.get("filename")
    if filename:
        out.append(filename)
    for sub in part.get("parts") or []:
        _walk_attachments(sub, out)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def get_text(self) -> str:
        return "".join(self._chunks)


def _html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    lines = [line.strip() for line in extractor.get_text().splitlines()]
    return "\n".join(line for line in lines if line).strip()
