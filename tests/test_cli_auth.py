from unittest.mock import MagicMock

from mail_ingestor import main as main_mod
from mail_ingestor.gmail.auth import GmailAuthError


def test_cli_auth_success(monkeypatch, capsys):
    monkeypatch.setattr(
        "mail_ingestor.gmail.auth.ensure_credentials", MagicMock(return_value=MagicMock())
    )
    monkeypatch.setattr("mail_ingestor.gmail.auth.GmailAuthConfig.from_env", classmethod(
        lambda cls, load_dotenv_file=True: cls()
    ))
    rc = main_mod.main(["auth"])
    assert rc == 0
    assert "auth ok" in capsys.readouterr().out.lower()


def test_cli_auth_failure_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(
        "mail_ingestor.gmail.auth.ensure_credentials",
        MagicMock(side_effect=GmailAuthError("boom")),
    )
    monkeypatch.setattr("mail_ingestor.gmail.auth.GmailAuthConfig.from_env", classmethod(
        lambda cls, load_dotenv_file=True: cls()
    ))
    rc = main_mod.main(["auth"])
    assert rc == 1
    captured = capsys.readouterr()
    assert "failed" in captured.err.lower()
