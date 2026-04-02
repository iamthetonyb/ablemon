"""Top-level package entrypoint for ABLE."""

from __future__ import annotations

import argparse
import asyncio
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="able",
        description="ABLE — Autonomous Business & Learning Engine.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Run the packaged gateway service.")
    chat_parser = subparsers.add_parser(
        "chat",
        help="Start the local terminal chat.",
    )
    from able.cli.chat import configure_parser as configure_chat_parser
    configure_chat_parser(chat_parser)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Console-script entrypoint.

    Running bare ``able`` in an interactive terminal defaults to chat.
    Running ``able`` non-interactively (systemd, cron) defaults to serve.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        # Interactive terminal → chat, background → serve
        if sys.stdin.isatty():
            args = parser.parse_args(["chat"] + (argv or []))
        else:
            from able.start import main as async_main
            asyncio.run(async_main())
            return

    if args.command == "serve":
        from able.start import main as async_main
        asyncio.run(async_main())
        return

    if args.command == "chat":
        from able.cli.chat import run_chat
        raise SystemExit(asyncio.run(run_chat(args)))

    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
