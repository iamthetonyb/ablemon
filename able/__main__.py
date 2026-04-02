"""Top-level package entrypoint for ABLE."""

from __future__ import annotations

import argparse
import asyncio

from able.cli.chat import configure_parser as configure_chat_parser, run_chat
from able.start import main as async_main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="able",
        description="ABLE runtime entrypoints.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Run the packaged gateway service.")
    chat_parser = subparsers.add_parser(
        "chat",
        help="Run the local terminal operator chat.",
    )
    configure_chat_parser(chat_parser)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Console-script entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in (None, "serve"):
        asyncio.run(async_main())
        return

    if args.command == "chat":
        asyncio.run(run_chat(args))
        return

    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
