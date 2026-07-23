"""CLI entrypoint. Pipeline wiring lands in a later task — this is a stub."""

from __future__ import annotations

import argparse


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"[mail-ingestor] scaffold OK. Target label: {args.label!r}")
    print("[mail-ingestor] pipeline not implemented yet (scaffold task EFSP-328).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
