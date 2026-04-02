"""Runtime boundary tests for optional subsystems."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_python(code: str, *, extra_env: dict[str, str | None] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        for key, value in extra_env.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_chat_help_path_stays_off_optional_runtime_modules():
    proc = _run_python(
        """
        import sys
        import able.__main__ as entry

        parser = entry.build_parser()
        try:
            parser.parse_args(["chat", "--help"])
        except SystemExit:
            pass

        blocked = (
            "able.start",
            "able.core.gateway.gateway",
            "able.billing",
            "billing",
            "able.channels",
            "channels",
            "able.core.federation.sync",
            "arize_phoenix",
            "opentelemetry",
            "telegram",
            "aiohttp",
        )
        loaded = sorted(
            name
            for name in sys.modules
            if any(name == prefix or name.startswith(prefix + ".") for prefix in blocked)
        )
        assert not loaded, loaded
        """
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_webhook_server_skips_billing_imports_without_payment_env():
    proc = _run_python(
        """
        import sys
        from able.tools.webhooks.server import WebhookServer

        server = WebhookServer()
        server.build_app()

        loaded = sorted(
            name
            for name in sys.modules
            if name == "able.billing" or name.startswith("able.billing.")
            or name == "billing" or name.startswith("billing.")
        )
        assert not loaded, loaded
        """,
        extra_env={
            "STRIPE_ENABLED": "false",
            "STRIPE_SECRET_KEY": None,
            "X402_ENABLED": "false",
            "X402_PAY_TO_ADDRESS": None,
        },
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_webhook_server_can_bootstrap_stripe_when_enabled():
    proc = _run_python(
        """
        import sys
        from able.tools.webhooks.server import WebhookServer

        server = WebhookServer()
        server.build_app()

        loaded = sorted(name for name in sys.modules if name.startswith("able.billing."))
        assert "able.billing.stripe_billing" in loaded, loaded
        """,
        extra_env={
            "STRIPE_ENABLED": "true",
            "STRIPE_SECRET_KEY": None,
            "X402_ENABLED": "false",
        },
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_channels_package_imports_cleanly():
    proc = _run_python(
        """
        import able.channels as channels
        assert "UnifiedGateway" in channels.__all__
        assert "ChannelAdapter" in channels.__all__
        """
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
