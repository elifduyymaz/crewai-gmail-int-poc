"""CLI entrypoint for the Gmail-ingestion PoC.

The bootstrap order in :func:`_bootstrap` is load-bearing:

    1. ``load_dotenv()``                     — populate env from ``.env``
    2. ``CREWAI_TELEMETRY_OPT_OUT=1``        — MUST land before any
       ``import crewai`` reaches the interpreter (NFR-S3, FR23)
    3. ``Settings.from_env()``               — fail-fast on missing env
    4. ``logging.basicConfig(...)``          — configure root before any
       ``logger.foo(...)`` call so records are not swallowed by a
       last-resort handler at WARNING
    5. deferred imports of :mod:`mail_ingestor.flow` (and other modules
       that transitively touch ``crewai``) — safe now that (2) is done

Rewiring this order can leak CrewAI telemetry, use unconfigured
loggers, or hide a config error behind a broken traceback. Keep the
sequence intact.

The batch loop here is the *happy path*: one exception aborts the run.
Story 5.2 replaces the naked loop with a ``DlqWriter.capture`` boundary
so per-message failures route to the DLQ table without aborting.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import only for type hints
    from mail_ingestor.config import Settings
    from mail_ingestor.schemas import SummaryRecord

_ERR_CONFIG = 1
_ERR_USAGE = 2

# Canonical structured-log format for the root logger. Any change here
# needs to update parsers or dashboards that consume the log stream.
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _positive_int(value: str) -> int:
    """argparse ``type=`` for ``--limit``: rejects zero and negative values.

    Without this, ``--limit -1`` and ``--limit 0`` were accepted, the reader
    returned an empty list, and the run exited 0 as if the user had asked
    for a legitimate empty batch — classic silent-success-on-garbage-input.
    """
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"--limit must be >= 1 (got {parsed})")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser.

    Two mutually-exclusive top-level modes:

      * ``--label LABEL --limit N`` — process the first N messages under
        the given Gmail label. ``--limit`` defaults to 5.
      * ``--demo``                  — reserved for Story 5.3's offline
        fixtures mode; currently exits with a friendly stub message.

    The legacy ``auth`` positional command (Task 2.1) is preserved so
    ``python -m mail_ingestor.main auth`` continues to run the OAuth
    setup without needing the full ANTHROPIC_AUTH_TOKEN bootstrap.
    """
    parser = argparse.ArgumentParser(
        prog="mail-ingestor",
        description="Summarize Gmail-labeled emails into structured records (PoC).",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("auth", help="Run the Gmail OAuth installed-app setup.")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--label",
        type=str,
        default=None,
        help="Gmail label to read messages from.",
    )
    mode.add_argument(
        "--demo",
        action="store_true",
        help="Run against committed offline fixtures (Story 5.3, not yet implemented).",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=5,
        help="Maximum number of messages to process (default: 5, must be >= 1).",
    )
    return parser


def _bootstrap(*, load_dotenv_file: bool = True) -> Settings:
    """Prepare the process for a batch run; see module docstring for order.

    ``load_dotenv_file=False`` bypasses the ``.env`` read so tests exercising
    a monkeypatched environment do not have the local ``.env`` inject values
    behind their back.
    """
    if load_dotenv_file:
        from dotenv import load_dotenv

        load_dotenv()
    os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "1")

    # Import Settings only after ``load_dotenv`` so any startup import that
    # touches env vars sees the ``.env`` values.
    from mail_ingestor.config import Settings

    settings = Settings.from_env(load_dotenv_file=False)
    _configure_logging(settings.log_level)
    # Boot-complete sentinel so an operator can tell "bootstrap succeeded
    # but the batch produced no output" from "bootstrap crashed". Never
    # emits the auth token (Settings.__repr__ masks it via field(repr=False)).
    logging.getLogger(__name__).info(
        "boot_complete model=%s db=%s log_level=%s",
        settings.llm_model,
        settings.sqlite_db_path,
        settings.log_level,
    )
    return settings


def _configure_logging(level_name: str) -> None:
    """Configure the root logger with the standard structured format.

    Deliberately does NOT pass ``force=True``: that would tear down any
    handlers already installed on the root logger (notably pytest's
    ``caplog``) and break capture during tests. Instead we let
    ``basicConfig`` no-op on repeat calls and always set the level
    explicitly so verbosity control still works.
    """
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(level=level, format=LOG_FORMAT)
    logging.getLogger().setLevel(level)


def _run_auth() -> int:
    from mail_ingestor.gmail.auth import GmailAuthConfig, GmailAuthError, ensure_credentials

    config = GmailAuthConfig.from_env()
    try:
        ensure_credentials(config)
    except GmailAuthError as exc:
        print(f"[mail-ingestor] Gmail auth failed: {exc}", file=sys.stderr)
        return _ERR_CONFIG
    print(f"[mail-ingestor] Gmail auth OK. Token saved to {config.token_path}.")
    return 0


def _build_gmail_client() -> Any:
    """Build an authenticated Gmail API client (deferred import).

    The google-api-python-client discovery cache write is disabled — it
    tries to write to ``~/.cache`` and emits a WARNING that would leak
    into our log stream; also PoC runs are short-lived so no benefit.
    """
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    from mail_ingestor.gmail.auth import GmailAuthConfig, ensure_credentials

    auth_config = GmailAuthConfig.from_env(load_dotenv_file=False)
    creds = ensure_credentials(auth_config)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _process_one(
    flow: Any,
    vault: Any,
    message_id: str,
    logger: logging.Logger,
) -> SummaryRecord:
    """Run the flow for one message, write the record, emit success log/stdout.

    Returns the SummaryRecord. A per-message exception aborts the batch —
    Story 5.2 replaces this call site with a ``DlqWriter.capture`` block.
    """
    started_ns = time.monotonic_ns()
    record = flow.kickoff(inputs={"message_id": message_id})
    vault.write_summary(record)
    print(record.model_dump_json())
    duration_ms = (time.monotonic_ns() - started_ns) // 1_000_000
    logger.info(
        "summary_completed message_id=%s tokens_prompt=%s tokens_completion=%s duration_ms=%d",
        record.source_message_id,
        record.tokens_prompt,
        record.tokens_completion,
        duration_ms,
    )
    return record  # type: ignore[no-any-return]


def _run_batch(settings: Settings, label: str, limit: int) -> int:
    """Process the first ``limit`` messages under ``label``.

    Startup errors (bad OAuth, unknown label, DB open failure, malformed
    token JSON, token-refresh network failure) print a friendly stderr
    line and return :data:`_ERR_CONFIG`. Per-message runtime errors
    propagate — Story 5.2 wraps the inner call in ``DlqWriter.capture``.
    """
    logger = logging.getLogger(__name__)

    # Deferred imports so any of these that transitively touches
    # ``crewai`` runs *after* CREWAI_TELEMETRY_OPT_OUT is set in _bootstrap.
    from google.auth.exceptions import GoogleAuthError

    from mail_ingestor.flow import MailIngestorFlow
    from mail_ingestor.gmail.auth import GmailAuthError
    from mail_ingestor.gmail.labels import LabelNotFoundError, LabelResolver
    from mail_ingestor.gmail.reader import GmailReaderService
    from mail_ingestor.persistence.db import init_db
    from mail_ingestor.persistence.writer import VaultWriter
    from mail_ingestor.telemetry import log_run_totals

    # Everything before the per-message loop is "startup": prerequisites
    # that must succeed before we can process a single message. A failure
    # here is not a per-message concern → route to stderr + exit 1 per the
    # Task 5.1 contract, don't leak a raw traceback.
    #
    # OSError covers FileNotFoundError / PermissionError on the token file.
    # GoogleAuthError covers RefreshError / TransportError from google-auth.
    # ValueError covers malformed token JSON in
    # ``Credentials.from_authorized_user_file``.
    # sqlite3.OperationalError covers disk-full / read-only FS / bad path
    # at ``init_db``.
    try:
        gmail_client = _build_gmail_client()
        resolver = LabelResolver(gmail_client)
        reader = GmailReaderService(gmail_client)
        label_id = resolver.resolve(label)
        conn = init_db(settings.sqlite_db_path)
        vault = VaultWriter(conn)
        flow = MailIngestorFlow(reader=reader, model=settings.llm_model)
        message_ids = reader.list_message_ids(label_id, max_results=limit)
    except LabelNotFoundError as exc:
        print(f"[mail-ingestor] {exc}", file=sys.stderr)
        return _ERR_CONFIG
    except GmailAuthError as exc:
        print(f"[mail-ingestor] Gmail auth failed: {exc}", file=sys.stderr)
        return _ERR_CONFIG
    except GoogleAuthError as exc:
        print(
            f"[mail-ingestor] Google auth failed ({type(exc).__name__}): {exc}",
            file=sys.stderr,
        )
        return _ERR_CONFIG
    except sqlite3.OperationalError as exc:
        print(
            f"[mail-ingestor] SQLite bootstrap failed at {settings.sqlite_db_path}: {exc}",
            file=sys.stderr,
        )
        return _ERR_CONFIG
    except (OSError, ValueError) as exc:
        print(
            f"[mail-ingestor] startup error ({type(exc).__name__}): {exc}",
            file=sys.stderr,
        )
        return _ERR_CONFIG

    if not message_ids:
        logger.info("no_messages label=%s limit=%d", label, limit)

    records: list[SummaryRecord] = []
    for message_id in message_ids:
        records.append(_process_one(flow, vault, message_id, logger))

    log_run_totals(records)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "auth":
        return _run_auth()

    if args.demo:
        print(
            "[mail-ingestor] --demo mode is Story 5.3 scope; not yet implemented.",
            file=sys.stderr,
        )
        return _ERR_USAGE

    if not args.label:
        parser.print_usage(sys.stderr)
        print(
            "[mail-ingestor] one of --label LABEL, --demo, or the 'auth' "
            "subcommand is required.",
            file=sys.stderr,
        )
        return _ERR_USAGE

    from mail_ingestor.config import MissingSettingError

    try:
        settings = _bootstrap()
    except MissingSettingError as exc:
        print(f"[mail-ingestor] configuration error: {exc}", file=sys.stderr)
        return _ERR_CONFIG

    return _run_batch(settings, args.label, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
