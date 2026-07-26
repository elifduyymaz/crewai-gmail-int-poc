from unittest.mock import MagicMock

import pytest

from mail_ingestor.gmail.labels import LabelNotFoundError, LabelResolver

LABELS = [
    {"id": "Label_1", "name": "poc/reports", "type": "user"},
    {"id": "INBOX", "name": "INBOX", "type": "system"},
]


def _service(labels):
    service = MagicMock()
    service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": labels
    }
    return service


def _execute_mock(service):
    return service.users.return_value.labels.return_value.list.return_value.execute


def test_resolve_returns_id():
    resolver = LabelResolver(_service(LABELS))
    assert resolver.resolve("poc/reports") == "Label_1"
    assert resolver.resolve("INBOX") == "INBOX"


def test_resolve_caches_single_api_call():
    service = _service(LABELS)
    resolver = LabelResolver(service)
    resolver.resolve("poc/reports")
    resolver.resolve("INBOX")
    resolver.resolve("poc/reports")
    assert _execute_mock(service).call_count == 1


def test_resolve_uses_user_id_me_by_default():
    service = _service(LABELS)
    LabelResolver(service).resolve("poc/reports")
    service.users.return_value.labels.return_value.list.assert_called_once_with(userId="me")


def test_resolve_custom_user_id():
    service = _service(LABELS)
    LabelResolver(service, user_id="user@x.com").resolve("poc/reports")
    service.users.return_value.labels.return_value.list.assert_called_once_with(userId="user@x.com")


def test_resolve_missing_raises_listing_available():
    resolver = LabelResolver(_service(LABELS))
    with pytest.raises(LabelNotFoundError, match="poc/reports"):
        resolver.resolve("nonexistent")


def test_resolve_empty_labels_raises():
    resolver = LabelResolver(_service([]))
    with pytest.raises(LabelNotFoundError):
        resolver.resolve("anything")


def test_refresh_refetches():
    service = _service(LABELS)
    resolver = LabelResolver(service)
    resolver.resolve("poc/reports")
    resolver.refresh()
    resolver.resolve("poc/reports")
    assert _execute_mock(service).call_count == 2


def test_missing_labels_key_treated_as_empty():
    service = MagicMock()
    service.users.return_value.labels.return_value.list.return_value.execute.return_value = {}
    with pytest.raises(LabelNotFoundError):
        LabelResolver(service).resolve("anything")
