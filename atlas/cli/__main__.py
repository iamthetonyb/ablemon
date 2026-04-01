"""Entry point for `python -m atlas.cli`."""

import argparse
import asyncio
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="ATLAS CLI Agent")
    parser.add_argument("--offline", action="store_true", help="Force offline mode (Ollama)")
    parser.add_argument("--local", action="store_true", help="Alias for --offline")
    parser.add_argument("--tier", type=int, choices=[1, 2, 4, 5], help="Force model tier")
    parser.add_argument("--tenant", default="tony", help="Tenant ID")
    parser.add_argument("--resume", metavar="SESSION_ID", help="Resume a previous session")
    parser.add_argument(
        "-c", "--command", metavar="CMD", help="One-shot: run CMD and exit"
    )
    parser.add_argument(
        "--safe", action="store_true", default=True, help="Ask before destructive writes (default)"
    )
    parser.add_argument("--auto", action="store_true", help="Skip write confirmations")
    args = parser.parse_args()

    from atlas.cli.repl import ATLASRepl, REPLConfig

    config = REPLConfig(
        model_tier=args.tier,
        tenant_id=args.tenant,
        offline=args.offline or args.local,
        safe_mode=not args.auto,
    )

    repl = ATLASRepl(config=config)

    # Resume previous session
    if args.resume:
        from atlas.cli.history import SessionHistory

        sh = SessionHistory(config.session_dir)
        if sh.session_exists(args.resume):
            repl.messages = sh.rebuild_messages(args.resume)
            repl.session_id = args.resume

    # One-shot mode
    if args.command:
        result = asyncio.run(repl.process_message(args.command))
        print(result)
        return

    # Interactive REPL
    asyncio.run(repl.run())


if __name__ == "__main__":
    main()
