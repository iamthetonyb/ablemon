"""Top-level package entrypoint for ABLE."""

from __future__ import annotations

import argparse
import asyncio
import sys


def build_parser(configure_chat: bool = False) -> argparse.ArgumentParser:
    """Build the CLI argument parser. Public API for test access."""
    return _build_parser(configure_chat=configure_chat)


def _build_parser(configure_chat: bool = False) -> argparse.ArgumentParser:
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
    if configure_chat:
        from able.cli.chat import configure_parser as configure_chat_parser
        configure_chat_parser(chat_parser)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Console-script entrypoint.

    Running bare ``able`` in an interactive terminal defaults to chat.
    Running ``able`` non-interactively (systemd, cron) defaults to serve.
    """
    # Lightweight parse — detect command without importing heavy modules.
    # parse_known_args lets us defer chat-specific arg validation.
    parser = _build_parser(configure_chat=False)
    args, remaining = parser.parse_known_args(argv)

    if args.command is None:
        if sys.stdin.isatty():
            args.command = "chat"
        else:
            args.command = "serve"

    if args.command == "serve":
        # Reject unknown args — misconfigured systemd/cron should fail loudly
        if remaining:
            parser.error(f"unrecognized arguments: {' '.join(remaining)}")
        from able.start import main as async_main
        asyncio.run(async_main())
        return

    if args.command == "chat":
        # Full re-parse with chat-specific arguments (pays ~120ms import here only)
        full_parser = _build_parser(configure_chat=True)
        chat_argv = argv if argv and len(argv) > 0 and argv[0] == "chat" else ["chat"] + (argv or [])
        chat_args = full_parser.parse_args(chat_argv)
        from able.cli.chat import run_chat
        raise SystemExit(asyncio.run(run_chat(chat_args)))

    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
