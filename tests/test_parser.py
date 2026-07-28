import base64
from datetime import UTC, datetime

from mail_ingestor.gmail.parser import parse_gmail_message
from mail_ingestor.schemas import EmailMessage


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _plain_message():
    return {
        "id": "m1",
        "threadId": "t1",
        "labelIds": ["Label_1", "INBOX"],
        "snippet": "hello there",
        "internalDate": "1690000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "bob@example.com, carol@example.com"},
                {"name": "Subject", "value": "Hi"},
            ],
            "body": {"data": _b64("Hello, world!"), "size": 13},
        },
    }


def test_parse_plain_text_message():
    msg = parse_gmail_message(_plain_message())
    assert isinstance(msg, EmailMessage)
    assert msg.message_id == "m1"
    assert msg.thread_id == "t1"
    assert msg.subject == "Hi"
    assert msg.sender == "Alice <alice@example.com>"
    assert msg.labels == ["Label_1", "INBOX"]
    assert msg.body_text == "Hello, world!"
    assert msg.snippet == "hello there"
    assert msg.attachment_filenames == []


def test_recipients_parsed_from_to_header():
    msg = parse_gmail_message(_plain_message())
    assert msg.recipients == ["bob@example.com", "carol@example.com"]


def test_received_at_is_utc_from_internal_date():
    msg = parse_gmail_message(_plain_message())
    assert msg.received_at == datetime.fromtimestamp(1690000000000 / 1000, tz=UTC)
    assert msg.received_at.tzinfo is not None


def test_multipart_alternative_prefers_plain():
    raw = {
        "id": "m2",
        "internalDate": "1690000000000",
        "labelIds": [],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "From", "value": "a@x.com"}],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Plain body")}},
                {"mimeType": "text/html", "body": {"data": _b64("<p>HTML body</p>")}},
            ],
        },
    }
    assert parse_gmail_message(raw).body_text == "Plain body"


def test_html_fallback_when_no_plain_part():
    raw = {
        "id": "m3",
        "internalDate": "1690000000000",
        "payload": {
            "mimeType": "text/html",
            "headers": [{"name": "From", "value": "a@x.com"}],
            "body": {"data": _b64("<p>Hello <b>bold</b></p><p>Second &amp; last</p>")},
        },
    }
    assert parse_gmail_message(raw).body_text == "Hello bold\nSecond & last"


def test_multipart_mixed_collects_attachment_filenames():
    raw = {
        "id": "m4",
        "internalDate": "1690000000000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "From", "value": "a@x.com"}],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("See attachment")}},
                {
                    "mimeType": "application/pdf",
                    "filename": "report.pdf",
                    "body": {"attachmentId": "abc"},
                },
            ],
        },
    }
    msg = parse_gmail_message(raw)
    assert msg.body_text == "See attachment"
    assert msg.attachment_filenames == ["report.pdf"]


def test_nested_multipart_finds_plain_and_attachment():
    raw = {
        "id": "m5",
        "internalDate": "1690000000000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "From", "value": "a@x.com"}],
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64("nested plain")}},
                        {"mimeType": "text/html", "body": {"data": _b64("<p>nested html</p>")}},
                    ],
                },
                {"mimeType": "image/png", "filename": "pic.png", "body": {"attachmentId": "xyz"}},
            ],
        },
    }
    msg = parse_gmail_message(raw)
    assert msg.body_text == "nested plain"
    assert msg.attachment_filenames == ["pic.png"]


def test_missing_headers_default_to_empty():
    raw = {
        "id": "m6",
        "internalDate": "1690000000000",
        "payload": {"mimeType": "text/plain", "body": {"data": _b64("body only")}},
    }
    msg = parse_gmail_message(raw)
    assert msg.sender == ""
    assert msg.subject == ""
    assert msg.recipients == []
    assert msg.body_text == "body only"


def test_thread_and_snippet_optional():
    raw = {
        "id": "m7",
        "internalDate": "1690000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "From", "value": "a@x.com"}],
            "body": {"data": _b64("x")},
        },
    }
    msg = parse_gmail_message(raw)
    assert msg.thread_id is None
    assert msg.snippet is None


def test_headers_are_case_insensitive():
    raw = {
        "id": "m8",
        "internalDate": "1690000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "FROM", "value": "a@x.com"},
                {"name": "subject", "value": "Lower"},
            ],
            "body": {"data": _b64("x")},
        },
    }
    msg = parse_gmail_message(raw)
    assert msg.sender == "a@x.com"
    assert msg.subject == "Lower"
