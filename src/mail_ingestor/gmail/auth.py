"""Gmail OAuth 2.0 installed-app flow.

Framework-agnostic core — no CrewAI dependency. Thin wrappers over
``google-auth-oauthlib`` / ``google-auth`` to obtain and reuse read-only Gmail
credentials. Decoupled from the LLM ``Settings`` so auth needs no Anthropic key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
DEFAULT_SCOPES: tuple[str, ...] = (GMAIL_READONLY_SCOPE,)


class GmailAuthError(RuntimeError):
    """Raised when Gmail OAuth setup cannot complete."""


@dataclass(frozen=True, slots=True)
class GmailAuthConfig:
    """Paths and scopes for the Gmail installed-app OAuth flow."""

    credentials_path: Path = Path("credentials.json")
    token_path: Path = Path("token.json")
    scopes: tuple[str, ...] = DEFAULT_SCOPES

    @classmethod
    def from_env(cls, *, load_dotenv_file: bool = True) -> GmailAuthConfig:
        """Build config from GMAIL_CREDENTIALS_PATH / GMAIL_TOKEN_PATH (with defaults)."""
        if load_dotenv_file:
            load_dotenv()
        creds = os.environ.get("GMAIL_CREDENTIALS_PATH", "").strip() or "credentials.json"
        token = os.environ.get("GMAIL_TOKEN_PATH", "").strip() or "token.json"
        return cls(credentials_path=Path(creds), token_path=Path(token))


def _save_credentials(creds: Credentials, token_path: Path) -> None:
    """Persist credentials to ``token_path``, created with owner-only (0600) permissions."""
    fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(creds.to_json())
    token_path.chmod(0o600)


def load_saved_credentials(config: GmailAuthConfig) -> Credentials | None:
    """Return usable saved credentials, refreshing if expired; else ``None``."""
    if not config.token_path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(config.token_path), list(config.scopes))
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            return None
        _save_credentials(creds, config.token_path)
        return creds
    return None


def run_installed_app_flow(config: GmailAuthConfig) -> Credentials:
    """Run the interactive installed-app consent flow and save the token."""
    if not config.credentials_path.exists():
        raise GmailAuthError(
            f"OAuth client file not found: {config.credentials_path}. "
            "Download it from Google Cloud Console (OAuth client ID, Desktop app) "
            "and set GMAIL_CREDENTIALS_PATH (see .env.example)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(config.credentials_path), list(config.scopes)
    )
    creds = flow.run_local_server(port=0)
    _save_credentials(creds, config.token_path)
    return creds


def ensure_credentials(config: GmailAuthConfig) -> Credentials:
    """Return valid credentials: reuse a saved token, else run the consent flow."""
    creds = load_saved_credentials(config)
    if creds is not None:
        return creds
    return run_installed_app_flow(config)
