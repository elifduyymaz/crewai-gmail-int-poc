from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from crewai.tools import BaseTool

from mail_ingestor.gmail.labels import LabelNotFoundError, LabelResolver
from mail_ingestor.gmail.reader import GmailReaderError, GmailReaderService
from mail_ingestor.tools.gmail_tool import GmailGetMessageTool, GmailListByLabelTool


def test_list_by_label_resolves_then_lists() -> None:
    reader = MagicMock(spec=GmailReaderService)
    resolver = MagicMock(spec=LabelResolver)
    resolver.resolve.return_value = "Label_42"
    reader.list_message_ids.return_value = ["m1", "m2", "m3"]

    tool = GmailListByLabelTool(reader=reader, resolver=resolver)
    result = tool._run(label_name="INBOX", limit=3)

    resolver.resolve.assert_called_once_with("INBOX")
    reader.list_message_ids.assert_called_once_with("Label_42", max_results=3)
    assert result == ["m1", "m2", "m3"]


def test_list_by_label_propagates_label_not_found() -> None:
    reader = MagicMock(spec=GmailReaderService)
    resolver = MagicMock(spec=LabelResolver)
    resolver.resolve.side_effect = LabelNotFoundError("unknown label")

    tool = GmailListByLabelTool(reader=reader, resolver=resolver)
    with pytest.raises(LabelNotFoundError):
        tool._run(label_name="MISSING", limit=10)
    reader.list_message_ids.assert_not_called()


def test_list_by_label_propagates_reader_error() -> None:
    reader = MagicMock(spec=GmailReaderService)
    resolver = MagicMock(spec=LabelResolver)
    resolver.resolve.return_value = "Label_1"
    reader.list_message_ids.side_effect = GmailReaderError("upstream 500")

    tool = GmailListByLabelTool(reader=reader, resolver=resolver)
    with pytest.raises(GmailReaderError):
        tool._run(label_name="INBOX", limit=5)


def test_get_message_returns_raw_dict_from_reader() -> None:
    reader = MagicMock(spec=GmailReaderService)
    raw = {"id": "m1", "payload": {"headers": []}, "snippet": "hi"}
    reader.get_message.return_value = raw

    tool = GmailGetMessageTool(reader=reader)
    result = tool._run(message_id="m1")

    reader.get_message.assert_called_once_with("m1")
    assert result is raw


def test_get_message_propagates_reader_error() -> None:
    reader = MagicMock(spec=GmailReaderService)
    reader.get_message.side_effect = GmailReaderError("404")

    tool = GmailGetMessageTool(reader=reader)
    with pytest.raises(GmailReaderError):
        tool._run(message_id="m1")


def test_tools_expose_name_and_description() -> None:
    reader = MagicMock(spec=GmailReaderService)
    resolver = MagicMock(spec=LabelResolver)
    list_tool = GmailListByLabelTool(reader=reader, resolver=resolver)
    get_tool = GmailGetMessageTool(reader=reader)

    assert list_tool.name == "gmail_list_by_label"
    assert list_tool.description  # non-empty
    assert get_tool.name == "gmail_get_message"
    assert get_tool.description


def test_tools_are_basetool_subclasses() -> None:
    assert issubclass(GmailListByLabelTool, BaseTool)
    assert issubclass(GmailGetMessageTool, BaseTool)
