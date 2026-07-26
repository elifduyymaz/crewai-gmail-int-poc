from unittest.mock import MagicMock

from mail_ingestor import main as main_mod
from mail_ingestor.gmail.auth import GmailAuthError


def test_cli_auth_success(monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "GmailAuthConfig", MagicMock())
    monkeypatch.setattr(main_mod, "ensure_credentials", MagicMock())
    rc = main_mod.main(["auth"])
    assert rc == 0
    assert "auth ok" in capsys.readouterr().out.lower()


def test_cli_auth_failure_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "GmailAuthConfig", MagicMock())
    monkeypatch.setattr(
        main_mod, "ensure_credentials", MagicMock(side_effect=GmailAuthError("boom"))
    )
    rc = main_mod.main(["auth"])
    assert rc == 1
    assert "failed" in capsys.readouterr().out.lower()


def test_cli_default_still_scaffolds(capsys):
    rc = main_mod.main(["--label", "poc/reports"])
    assert rc == 0
    assert "poc/reports" in capsys.readouterr().out
