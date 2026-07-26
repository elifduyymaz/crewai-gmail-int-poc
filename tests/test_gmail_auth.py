from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mail_ingestor.gmail import auth
from mail_ingestor.gmail.auth import (
    GmailAuthConfig,
    GmailAuthError,
    ensure_credentials,
    load_saved_credentials,
    run_installed_app_flow,
)


def _config(tmp_path, *, creds_name="credentials.json", token_name="token.json"):
    return GmailAuthConfig(
        credentials_path=tmp_path / creds_name,
        token_path=tmp_path / token_name,
    )


def test_from_env_defaults(monkeypatch):
    monkeypatch.delenv("GMAIL_CREDENTIALS_PATH", raising=False)
    monkeypatch.delenv("GMAIL_TOKEN_PATH", raising=False)
    cfg = GmailAuthConfig.from_env(load_dotenv_file=False)
    assert cfg.credentials_path == Path("credentials.json")
    assert cfg.token_path == Path("token.json")
    assert cfg.scopes == ("https://www.googleapis.com/auth/gmail.readonly",)


def test_from_env_reads_overrides(monkeypatch):
    monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", "/secrets/client.json")
    monkeypatch.setenv("GMAIL_TOKEN_PATH", "/secrets/tok.json")
    cfg = GmailAuthConfig.from_env(load_dotenv_file=False)
    assert cfg.credentials_path == Path("/secrets/client.json")
    assert cfg.token_path == Path("/secrets/tok.json")


def test_load_saved_none_when_no_token(tmp_path):
    assert load_saved_credentials(_config(tmp_path)) is None


def test_load_saved_returns_valid(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.token_path.write_text("{}", encoding="utf-8")
    fake = MagicMock(valid=True)
    monkeypatch.setattr(
        auth, "Credentials", MagicMock(from_authorized_user_file=MagicMock(return_value=fake))
    )
    assert load_saved_credentials(cfg) is fake


def test_load_saved_refreshes_expired(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.token_path.write_text("{}", encoding="utf-8")
    fake = MagicMock(valid=False, expired=True, refresh_token="r")
    fake.to_json.return_value = '{"token": "new"}'
    monkeypatch.setattr(
        auth, "Credentials", MagicMock(from_authorized_user_file=MagicMock(return_value=fake))
    )
    monkeypatch.setattr(auth, "Request", MagicMock())
    result = load_saved_credentials(cfg)
    assert result is fake
    fake.refresh.assert_called_once()
    assert cfg.token_path.read_text(encoding="utf-8") == '{"token": "new"}'


def test_load_saved_none_when_expired_no_refresh(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.token_path.write_text("{}", encoding="utf-8")
    fake = MagicMock(valid=False, expired=True, refresh_token=None)
    monkeypatch.setattr(
        auth, "Credentials", MagicMock(from_authorized_user_file=MagicMock(return_value=fake))
    )
    assert load_saved_credentials(cfg) is None


def test_run_flow_missing_credentials_raises(tmp_path):
    with pytest.raises(GmailAuthError, match="OAuth client file not found"):
        run_installed_app_flow(_config(tmp_path))


def test_run_flow_success_saves_token(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.credentials_path.write_text("{}", encoding="utf-8")
    fake_creds = MagicMock()
    fake_creds.to_json.return_value = '{"token": "x"}'
    fake_flow = MagicMock()
    fake_flow.run_local_server.return_value = fake_creds
    fake_flow_cls = MagicMock()
    fake_flow_cls.from_client_secrets_file.return_value = fake_flow
    monkeypatch.setattr(auth, "InstalledAppFlow", fake_flow_cls)
    result = run_installed_app_flow(cfg)
    assert result is fake_creds
    fake_flow_cls.from_client_secrets_file.assert_called_once()
    fake_flow.run_local_server.assert_called_once()
    assert cfg.token_path.read_text(encoding="utf-8") == '{"token": "x"}'
    assert (cfg.token_path.stat().st_mode & 0o777) == 0o600


def test_ensure_reuses_saved(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    saved = MagicMock()
    monkeypatch.setattr(auth, "load_saved_credentials", MagicMock(return_value=saved))
    flow = MagicMock()
    monkeypatch.setattr(auth, "run_installed_app_flow", flow)
    assert ensure_credentials(cfg) is saved
    flow.assert_not_called()


def test_ensure_runs_flow_when_no_saved(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    monkeypatch.setattr(auth, "load_saved_credentials", MagicMock(return_value=None))
    made = MagicMock()
    monkeypatch.setattr(auth, "run_installed_app_flow", MagicMock(return_value=made))
    assert ensure_credentials(cfg) is made
