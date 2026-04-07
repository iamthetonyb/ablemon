"""
Command Guard - Allowlist-based command authorization
Blocklists have 8+ documented bypasses. Allowlists are secure by default.

Security patterns ported from Claude Code's BashTool (12K+ LOC):
- Binary hijack env var detection (LD_, DYLD_, PATH)
- Dangerous removal path checking (/,  /etc, /usr, ~)
- cd+git compound detection (bare repo fsmonitor RCE)
- Safe env var stripping for permission matching
- ``--`` end-of-options handling to prevent flag smuggling
- MAX_SUBCOMMANDS cap against DoS via exponential splitting
"""

from __future__ import annotations

import re
import shlex
import unicodedata
from pathlib import Path
from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class CommandVerdict(Enum):
    ALLOWED = "allowed"
    REQUIRES_APPROVAL = "requires_approval"
    DENIED = "denied"


@dataclass
class CommandAnalysis:
    verdict: CommandVerdict
    command: str
    base_command: str
    parsed_args: List[str]
    parsed_argv: List[str]
    reason: str
    risk_level: int  # 1-10
    uses_shell_syntax: bool = False


# ── Ported from Claude Code BashTool security layer ──────────────

# Binary hijack env vars — if set as prefix, the command can load
# arbitrary shared objects or redirect binary resolution.
_BINARY_HIJACK_RE = re.compile(r"^(LD_|DYLD_|PATH=)")

# Env vars that are safe to appear as command prefixes (build config,
# locale, terminal settings).  Matches Claude Code's SAFE_ENV_VARS.
_SAFE_ENV_VARS: frozenset[str] = frozenset({
    "GOEXPERIMENT", "GOOS", "GOARCH", "CGO_ENABLED", "GO111MODULE",
    "RUST_BACKTRACE", "RUST_LOG",
    "NODE_ENV",
    "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTEST_DEBUG",
    "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_TIME", "CHARSET",
    "TERM", "COLORTERM", "NO_COLOR", "FORCE_COLOR", "TZ",
    "LS_COLORS", "LSCOLORS", "GREP_COLOR", "GREP_COLORS",
    "TIME_STYLE", "BLOCK_SIZE", "BLOCKSIZE",
})

# Dangerous removal targets — rm/rmdir on these always requires approval
# regardless of allowlist rules.  Prevents catastrophic data loss.
_DANGEROUS_PATHS: frozenset[str] = frozenset({
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64",
    "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys",
    "/tmp", "/usr", "/var",
})

# DoS protection: compound commands split into more subcommands than
# this are force-escalated to REQUIRES_APPROVAL.
MAX_SUBCOMMANDS = 50

# ALLOWLIST - Only these commands can execute without approval
ALLOWED_COMMANDS = {
    # Safe read-only commands
    "ls": {"max_risk": 1, "allowed_args": ["-l", "-a", "-la", "-lah", "-h"]},
    "cat": {"max_risk": 2, "blocked_paths": ["/etc/shadow", "/etc/passwd", "~/.ssh"]},
    "head": {"max_risk": 1, "allowed_args": ["-n"]},
    "tail": {"max_risk": 1, "allowed_args": ["-n", "-f"]},
    "grep": {"max_risk": 2, "allowed_args": ["-r", "-i", "-n", "-l", "-c"]},
    "rg": {"max_risk": 2},
    "find": {"max_risk": 3, "blocked_args": ["-exec", "-delete"]},
    "wc": {"max_risk": 1},
    "sort": {"max_risk": 1},
    "uniq": {"max_risk": 1},
    "cut": {"max_risk": 1},
    "awk": {"max_risk": 3},  # Powerful but useful
    "sed": {"max_risk": 3, "blocked_args": ["-i"]},  # No in-place editing
    "diff": {"max_risk": 1},
    "echo": {"max_risk": 1},
    "pwd": {"max_risk": 1},
    "whoami": {"max_risk": 1},
    "date": {"max_risk": 1},
    "which": {"max_risk": 1},
    "type": {"max_risk": 1},
    "file": {"max_risk": 1},

    # Git commands (read-focused)
    "git": {
        "max_risk": 4,
        "allowed_subcommands": ["status", "log", "diff", "branch", "show", "ls-files", "remote", "fetch"],
        "requires_approval_subcommands": ["push", "commit", "merge", "rebase", "reset", "checkout"],
        "denied_subcommands": ["push --force", "reset --hard"]
    },

    # Python/Node for controlled execution
    "python": {"max_risk": 5, "requires_approval": True},
    "python3": {"max_risk": 5, "requires_approval": True},
    "node": {"max_risk": 5, "requires_approval": True},

    # Package managers (read-only by default)
    "pip": {"max_risk": 3, "allowed_subcommands": ["list", "show", "freeze"]},
    "npm": {"max_risk": 3, "allowed_subcommands": ["list", "ls", "view", "outdated"]},
}

# Commands that ALWAYS require human approval
APPROVAL_REQUIRED = {
    "mkdir", "touch", "cp", "mv",  # File creation/modification
    "pip install", "npm install",  # Package installation
    "git commit", "git push",      # Code changes
    "docker", "kubectl",           # Container operations
}

# Commands that are ALWAYS denied
ALWAYS_DENIED = {
    "rm", "rmdir",           # Deletion
    "sudo", "su",            # Privilege escalation
    "chmod", "chown",        # Permission changes
    "curl", "wget",          # Network downloads (use controlled fetch instead)
    "nc", "netcat",          # Network tools
    "ssh", "scp",            # Remote access
    "kill", "killall",       # Process termination
    "shutdown", "reboot",    # System control
    "dd",                    # Disk operations
    "mkfs", "fdisk",         # Disk formatting
    "iptables", "ufw",       # Firewall
    "crontab",               # Scheduled tasks
    "eval", "exec",          # Dynamic execution
    "source", ".",           # Script sourcing
}

class CommandGuard:
    def __init__(self, trust_tier: int = 1):
        self.trust_tier = trust_tier  # 1-4, higher = more permissions
        self._yaml_permissions = self._load_yaml_permissions()
        # Enhanced policy engine (supports priority ordering, globs, scopes)
        self._policy_engine = self._load_policy_engine()

    @staticmethod
    def _load_yaml_permissions() -> Optional[dict]:
        """Load tool_permissions.yaml if available. Falls back to hardcoded defaults."""
        yaml_path = Path(__file__).parent.parent.parent.parent / "config" / "tool_permissions.yaml"
        if not yaml_path.exists():
            return None
        try:
            import yaml
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                return None
            return data
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "tool_permissions.yaml is invalid — using hardcoded defaults"
            )
            return None

    @staticmethod
    def _load_policy_engine():
        """Load the enhanced policy engine if available."""
        try:
            from able.core.security.policy_engine import PolicyEngine
            yaml_path = Path(__file__).parent.parent.parent.parent / "config" / "tool_permissions.yaml"
            return PolicyEngine.from_yaml(yaml_path)
        except Exception:
            return None

    def _yaml_verdict(self, command: str) -> Optional[CommandVerdict]:
        """Check YAML permissions first. Returns verdict or None to fall through.

        Uses the enhanced PolicyEngine if available (supports priority ordering
        and glob patterns). Falls back to legacy 3-tier list matching.
        """
        # Try enhanced policy engine first
        if self._policy_engine:
            from able.core.security.policy_engine import PolicyAction
            verdict = self._policy_engine.evaluate(command)
            if verdict.matched:
                action_map = {
                    PolicyAction.ALLOW: CommandVerdict.ALLOWED,
                    PolicyAction.DENY: CommandVerdict.DENIED,
                    PolicyAction.REQUIRE_APPROVAL: CommandVerdict.REQUIRES_APPROVAL,
                }
                return action_map.get(verdict.action)

        # Legacy fallback: simple list matching
        if not self._yaml_permissions:
            return None

        cmd_lower = command.lower().strip()

        # Check never_allow first
        for pattern in self._yaml_permissions.get("never_allow", []):
            if cmd_lower == pattern or cmd_lower.startswith(pattern + " "):
                return CommandVerdict.DENIED

        # Check always_allow
        for pattern in self._yaml_permissions.get("always_allow", []):
            if cmd_lower == pattern or cmd_lower.startswith(pattern + " "):
                return CommandVerdict.ALLOWED

        # Check ask_before
        for pattern in self._yaml_permissions.get("ask_before", []):
            if cmd_lower == pattern or cmd_lower.startswith(pattern + " "):
                return CommandVerdict.REQUIRES_APPROVAL

        return None  # Fall through to hardcoded rules

    def _parse_command(self, command: str) -> Tuple[str, List[str], List[str]]:
        """Parse command into base command and arguments.

        Strips safe env var prefixes (NODE_ENV=prod, RUST_LOG=debug)
        before extracting the base command so that permission rules
        match the actual program, not the env setter.
        """
        try:
            parts = shlex.split(command, posix=True)
            if not parts:
                return "", [], []
            # Strip leading safe env var assignments
            i = 0
            while i < len(parts) and "=" in parts[i]:
                var_name = parts[i].split("=", 1)[0]
                if var_name not in _SAFE_ENV_VARS:
                    break
                i += 1
            if i >= len(parts):
                return parts[0], parts[1:], parts  # all env vars, no command
            return parts[i], parts[i + 1:], parts
        except ValueError:
            # Handle unbalanced quotes etc
            parts = command.split()
            return (
                parts[0] if parts else "",
                parts[1:] if len(parts) > 1 else [],
                parts,
            )

    def _tokenize_shell(self, command: str) -> List[str]:
        """Tokenize shell punctuation without losing quoted literals."""
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>")
            lexer.whitespace_split = True
            return list(lexer)
        except ValueError:
            return command.split()

    def _detect_shell_syntax(self, command: str) -> tuple[bool, Optional[str], bool]:
        """Detect shell operators that require a shell parser."""
        tokens = self._tokenize_shell(command)
        requires_approval_ops = {"|", "||", "&&", ";"}
        denied_ops = {">", ">>", "<", "<<", "<<<", "&"}

        for token in tokens:
            if token in denied_ops:
                return True, f"Shell operator '{token}' is not permitted", True
            if token in requires_approval_ops:
                return True, f"Shell operator '{token}' requires approval", False

        if re.search(r"(^|\s)\d*(?:>>?|<<?<?|>&)\S*", command):
            return True, "Shell redirection is not permitted", True

        return False, None, False

    @staticmethod
    def _contains_obfuscated_whitespace(command: str) -> bool:
        return any(
            ch not in {" ", "\t", "\n", "\r"}
            and unicodedata.category(ch).startswith("Z")
            for ch in command
        )

    @staticmethod
    def _contains_control_characters(command: str) -> bool:
        return any(ord(ch) < 32 and ch not in {"\t", "\n", "\r"} for ch in command)

    @staticmethod
    def _check_binary_hijack(command: str) -> Optional[str]:
        """Detect env var prefixes that hijack binary loading.

        LD_PRELOAD, DYLD_INSERT_LIBRARIES, and PATH= as command prefixes
        can redirect execution to attacker-controlled shared objects or
        binaries.  Ported from Claude Code's BINARY_HIJACK_VARS check.
        """
        for token in command.split():
            if "=" not in token:
                break  # past env var prefix region
            if _BINARY_HIJACK_RE.match(token):
                var_name = token.split("=", 1)[0]
                return f"Binary hijack env var: {var_name}"
        return None

    @staticmethod
    def _check_dangerous_removal(command: str) -> Optional[str]:
        """Detect rm/rmdir targeting critical system paths.

        Ported from Claude Code's checkDangerousRemovalPaths.  Commands
        like ``rm -rf /`` or ``rm -rf /usr`` are always escalated to
        REQUIRES_APPROVAL regardless of other allowlist rules.
        """
        parts = command.split()
        if not parts or parts[0] not in ("rm", "rmdir"):
            return None
        # Extract path arguments (skip flags, respect --)
        after_double_dash = False
        for arg in parts[1:]:
            if arg == "--":
                after_double_dash = True
                continue
            if not after_double_dash and arg.startswith("-"):
                continue
            # Resolve path
            p = arg.replace("~", str(Path.home()))
            resolved = str(Path(p).resolve()) if not Path(p).is_absolute() else p
            resolved = resolved.rstrip("/") or "/"
            if resolved in _DANGEROUS_PATHS:
                return f"Dangerous removal target: {resolved}"
        return None

    @staticmethod
    def _check_cd_git_compound(command: str) -> Optional[str]:
        """Detect cd+git in compound commands (bare repo fsmonitor RCE).

        ``cd malicious-repo && git status`` in a bare repo with a crafted
        fsmonitor hook executes arbitrary code.  Claude Code specifically
        gates this cross-segment pattern.
        """
        subcommands = re.split(r"\s*(?:&&|\|\||;)\s*", command)
        has_cd = any(s.strip().startswith("cd ") or s.strip() == "cd" for s in subcommands)
        has_git = any(s.strip().startswith("git ") or s.strip() == "git" for s in subcommands)
        if has_cd and has_git:
            return "cd+git compound: bare repo fsmonitor attack vector"
        return None

    def _check_dangerous_patterns(self, command: str) -> Optional[str]:
        """Check for shell injection patterns"""
        if self._contains_obfuscated_whitespace(command):
            return "Unicode whitespace obfuscation"
        if self._contains_control_characters(command):
            return "Control characters"

        # Binary hijack env vars (LD_, DYLD_, PATH=)
        hijack = self._check_binary_hijack(command)
        if hijack:
            return hijack

        # Dangerous removal paths
        removal = self._check_dangerous_removal(command)
        if removal:
            return removal

        # cd+git compound (bare repo attack)
        cdgit = self._check_cd_git_compound(command)
        if cdgit:
            return cdgit

        dangerous_patterns = [
            (r':\(\)\s*\{.*?\}\s*;\s*:', "Fork bomb"),
            (r'\$\(', "Command substitution"),
            (r'`[^`]+`', "Backtick execution"),
            (r'\b(?:zmodload|zpty|ztcp)\b', "Zsh advanced module loading"),
            (r'(?:^|\s)(?:IFS=|export\s+IFS=)', "IFS injection"),
            (r'/proc/(?:self|\d+)/environ', "Process environment scraping"),
            (r'\{[^{}\n]*,[^{}\n]*\}', "Brace expansion"),
            (r'(^|[^\\])#["\']', "Comment/quote desynchronization"),
            (r'\|\s*sh', "Pipe to shell"),
            (r'\|\s*bash', "Pipe to bash"),
            (r'\|\s*zsh', "Pipe to zsh"),
            (r';\s*rm', "Chained deletion"),
            (r'&&\s*rm', "Conditional deletion"),
            (r'\|\|\s*rm', "Fallback deletion"),
            (r'>\s*/etc/', "Write to system config"),
            (r'>\s*/dev/', "Write to device"),
            (r'2>&1.*\|', "Stderr redirect to pipe"),
        ]

        for pattern, description in dangerous_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return description

        sleep_match = re.match(r"^\s*sleep\s+(\d+(?:\.\d+)?)\b", command)
        if sleep_match and float(sleep_match.group(1)) >= 2:
            return "Long sleep polling is not permitted"
        return None

    def analyze(self, command: str) -> CommandAnalysis:
        """Analyze a command and return verdict"""
        base_cmd, args, argv = self._parse_command(command)
        uses_shell_syntax, shell_reason, shell_is_denied = self._detect_shell_syntax(command)

        # YAML never_allow checked first (hard deny always wins)
        yaml_verdict = self._yaml_verdict(command)
        if yaml_verdict == CommandVerdict.DENIED:
            return CommandAnalysis(
                verdict=CommandVerdict.DENIED,
                command=command,
                base_command=base_cmd,
                parsed_args=args,
                parsed_argv=argv,
                reason="YAML policy: denied",
                risk_level=9,
                uses_shell_syntax=uses_shell_syntax,
            )

        # DoS protection: cap subcommand count (ported from Claude Code)
        subcommands = re.split(r"\s*(?:&&|\|\||;|\|)\s*", command)
        if len(subcommands) > MAX_SUBCOMMANDS:
            return CommandAnalysis(
                verdict=CommandVerdict.REQUIRES_APPROVAL,
                command=command,
                base_command=base_cmd,
                parsed_args=args,
                parsed_argv=argv,
                reason=f"Compound command has {len(subcommands)} subcommands "
                       f"(cap: {MAX_SUBCOMMANDS}) — cannot verify safety",
                risk_level=8,
                uses_shell_syntax=True,
            )

        # Check for dangerous patterns first
        danger = self._check_dangerous_patterns(command)
        if danger:
            return CommandAnalysis(
                verdict=CommandVerdict.DENIED,
                command=command,
                base_command=base_cmd,
                parsed_args=args,
                parsed_argv=argv,
                reason=f"Dangerous pattern detected: {danger}",
                risk_level=10,
                uses_shell_syntax=uses_shell_syntax,
            )

        if shell_reason:
            return CommandAnalysis(
                verdict=CommandVerdict.DENIED if shell_is_denied else CommandVerdict.REQUIRES_APPROVAL,
                command=command,
                base_command=base_cmd,
                parsed_args=args,
                parsed_argv=argv,
                reason=shell_reason,
                risk_level=8 if shell_is_denied else 6,
                uses_shell_syntax=uses_shell_syntax,
            )

        # YAML allow/ask_before (checked after dangerous patterns/shell syntax)
        if yaml_verdict is not None:
            return CommandAnalysis(
                verdict=yaml_verdict,
                command=command,
                base_command=base_cmd,
                parsed_args=args,
                parsed_argv=argv,
                reason=f"YAML policy: {yaml_verdict.value}",
                risk_level={"allowed": 1, "requires_approval": 5}.get(yaml_verdict.value, 5),
                uses_shell_syntax=uses_shell_syntax,
            )

        # Check if always denied
        if base_cmd in ALWAYS_DENIED:
            return CommandAnalysis(
                verdict=CommandVerdict.DENIED,
                command=command,
                base_command=base_cmd,
                parsed_args=args,
                parsed_argv=argv,
                reason=f"Command '{base_cmd}' is not permitted",
                risk_level=10,
                uses_shell_syntax=uses_shell_syntax,
            )

        # Check if always requires approval
        for approval_cmd in APPROVAL_REQUIRED:
            if command.startswith(approval_cmd):
                return CommandAnalysis(
                    verdict=CommandVerdict.REQUIRES_APPROVAL,
                    command=command,
                    base_command=base_cmd,
                    parsed_args=args,
                    parsed_argv=argv,
                    reason=f"Command '{approval_cmd}' requires human approval",
                    risk_level=6,
                    uses_shell_syntax=uses_shell_syntax,
                )

        # Check allowlist
        if base_cmd in ALLOWED_COMMANDS:
            config = ALLOWED_COMMANDS[base_cmd]
            risk = config.get("max_risk", 5)

            # Check for blocked arguments
            blocked_args = config.get("blocked_args", [])
            for arg in args:
                if arg in blocked_args:
                    return CommandAnalysis(
                        verdict=CommandVerdict.DENIED,
                        command=command,
                        base_command=base_cmd,
                        parsed_args=args,
                        parsed_argv=argv,
                        reason=f"Argument '{arg}' not permitted for '{base_cmd}'",
                        risk_level=8,
                        uses_shell_syntax=uses_shell_syntax,
                    )

            # Check for blocked paths
            blocked_paths = config.get("blocked_paths", [])
            for arg in args:
                for blocked in blocked_paths:
                    if blocked in arg:
                        return CommandAnalysis(
                            verdict=CommandVerdict.DENIED,
                            command=command,
                            base_command=base_cmd,
                            parsed_args=args,
                            parsed_argv=argv,
                            reason=f"Path '{arg}' not permitted",
                            risk_level=8,
                            uses_shell_syntax=uses_shell_syntax,
                        )

            # Check subcommands for git, pip, npm etc
            if "allowed_subcommands" in config and args:
                subcommand = args[0]
                if subcommand in config.get("denied_subcommands", []):
                    return CommandAnalysis(
                        verdict=CommandVerdict.DENIED,
                        command=command,
                        base_command=base_cmd,
                        parsed_args=args,
                        parsed_argv=argv,
                        reason=f"Subcommand '{subcommand}' is denied",
                        risk_level=9,
                        uses_shell_syntax=uses_shell_syntax,
                    )
                if subcommand in config.get("requires_approval_subcommands", []):
                    return CommandAnalysis(
                        verdict=CommandVerdict.REQUIRES_APPROVAL,
                        command=command,
                        base_command=base_cmd,
                        parsed_args=args,
                        parsed_argv=argv,
                        reason=f"Subcommand '{subcommand}' requires approval",
                        risk_level=6,
                        uses_shell_syntax=uses_shell_syntax,
                    )
                if subcommand not in config["allowed_subcommands"]:
                    return CommandAnalysis(
                        verdict=CommandVerdict.REQUIRES_APPROVAL,
                        command=command,
                        base_command=base_cmd,
                        parsed_args=args,
                        parsed_argv=argv,
                        reason=f"Subcommand '{subcommand}' not in allowlist",
                        risk_level=5,
                        uses_shell_syntax=uses_shell_syntax,
                    )

            # Check if command requires approval regardless
            if config.get("requires_approval"):
                # But trust tier 4 can bypass
                if self.trust_tier >= 4:
                    return CommandAnalysis(
                        verdict=CommandVerdict.ALLOWED,
                        command=command,
                        base_command=base_cmd,
                        parsed_args=args,
                        parsed_argv=argv,
                        reason=f"Allowed for trust tier {self.trust_tier}",
                        risk_level=risk,
                        uses_shell_syntax=uses_shell_syntax,
                    )
                return CommandAnalysis(
                    verdict=CommandVerdict.REQUIRES_APPROVAL,
                    command=command,
                    base_command=base_cmd,
                    parsed_args=args,
                    parsed_argv=argv,
                    reason=f"Command requires approval at trust tier {self.trust_tier}",
                    risk_level=risk,
                    uses_shell_syntax=uses_shell_syntax,
                )

            return CommandAnalysis(
                verdict=CommandVerdict.ALLOWED,
                command=command,
                base_command=base_cmd,
                parsed_args=args,
                parsed_argv=argv,
                reason=f"Command '{base_cmd}' is in allowlist",
                risk_level=risk,
                uses_shell_syntax=uses_shell_syntax,
            )

        # Not in allowlist = requires approval
        return CommandAnalysis(
            verdict=CommandVerdict.REQUIRES_APPROVAL,
            command=command,
            base_command=base_cmd,
            parsed_args=args,
            parsed_argv=argv,
            reason=f"Command '{base_cmd}' not in allowlist",
            risk_level=7,
            uses_shell_syntax=uses_shell_syntax,
        )
