"""Top-level package entrypoint for ABLE."""

from __future__ import annotations

import asyncio

from able.start import main as async_main


def main() -> None:
    """Console-script entrypoint."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
