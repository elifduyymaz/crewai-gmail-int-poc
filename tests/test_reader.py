from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from mail_ingestor.gmail.reader import GmailReaderError, GmailReaderService


def _http_error(status=500):
    return HttpError(httplib2.Response({"status": status}), b"boom")


def _list_execute(service):
    return service.users.return_value.messages.return_value.list.return_value.execute


def _list_call(service):
    return service.users.return_value.messages.return_value.list


def _service_list(pages):
    """`pages`: successive dicts returned by list().execute()."""
    service = MagicMock()
    _list_execute(service).side_effect = pages
    return service


def test_list_single_page():
    service = _service_list([{"messages": [{"id": "m1"}, {"id": "m2"}]}])
    assert GmailReaderService(service).list_message_ids("Label_1") == ["m1", "m2"]


def test_list_paginates():
    service = _service_list(
        [
            {"messages": [{"id": "m1"}], "nextPageToken": "t1"},
            {"messages": [{"id": "m2"}]},
        ]
    )
    assert GmailReaderService(service).list_message_ids("Label_1") == ["m1", "m2"]
    assert _list_execute(service).call_count == 2


def test_list_empty_label():
    service = _service_list([{}])
    assert GmailReaderService(service).list_message_ids("Label_1") == []


def test_list_respects_max_results():
    service = _service_list(
        [{"messages": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}], "nextPageToken": "t1"}]
    )
    assert GmailReaderService(service).list_message_ids("Label_1", max_results=2) == ["m1", "m2"]


def test_list_passes_user_and_label():
    service = _service_list([{"messages": []}])
    GmailReaderService(service, user_id="u@x").list_message_ids("Label_9")
    _list_call(service).assert_called_with(userId="u@x", labelIds=["Label_9"])


def test_list_wraps_http_error():
    service = MagicMock()
    _list_execute(service).side_effect = _http_error(500)
    with pytest.raises(GmailReaderError, match="Label_1"):
        GmailReaderService(service).list_message_ids("Label_1")


def test_get_message_full_format():
    service = MagicMock()
    get_execute = service.users.return_value.messages.return_value.get.return_value.execute
    get_execute.return_value = {"id": "m1", "payload": {}}
    assert GmailReaderService(service).get_message("m1") == {"id": "m1", "payload": {}}
    service.users.return_value.messages.return_value.get.assert_called_once_with(
        userId="me", id="m1", format="full"
    )


def test_get_message_wraps_http_error():
    service = MagicMock()
    service.users.return_value.messages.return_value.get.return_value.execute.side_effect = (
        _http_error(404)
    )
    with pytest.raises(GmailReaderError, match="m1"):
        GmailReaderService(service).get_message("m1")
