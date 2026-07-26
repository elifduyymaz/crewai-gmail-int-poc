"""CLI entrypoint for the Gmail-ingestion PoC."""

from __future__ import annotations

import argparse

from mail_ingestor.gmail.auth import GmailAuthConfig, GmailAuthError, ensure_credentials


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mail-ingestor",
        description="Summarize Gmail-labeled emails into structured records (PoC).",
    )
    parser.add_argument(
        "--label",
        default="poc/reports",
        help="Gmail label to read messages from.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("auth", help="Run the Gmail OAuth installed-app setup.")
    return parser


def _run_auth() -> int:
    config = GmailAuthConfig.from_env()
    try:
        ensure_credentials(config)
    except GmailAuthError as exc:
        print(f"[mail-ingestor] Gmail auth failed: {exc}")
        return 1
    print(f"[mail-ingestor] Gmail auth OK. Token saved to {config.token_path}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "auth":
        return _run_auth()
    print(f"[mail-ingestor] scaffold OK. Target label: {args.label!r}")
    print("[mail-ingestor] pipeline not implemented yet (scaffold task EFSP-328).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
