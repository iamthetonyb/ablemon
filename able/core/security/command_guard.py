"""
Command Guard - Allowlist-based command authorization
Blocklists have 8+ documented bypasses. Allowlists are secure by default.
"""

from __future__ import annotations

import re
import shlex
import unicodedata
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

    def _parse_command(self, command: str) -> Tuple[str, List[str], List[str]]:
        """Parse command into base command and arguments"""
        try:
            parts = shlex.split(command, posix=True)
            if not parts:
                return "", [], []
            return parts[0], parts[1:], parts
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

    def _check_dangerous_patterns(self, command: str) -> Optional[str]:
        """Check for shell injection patterns"""
        if self._contains_obfuscated_whitespace(command):
            return "Unicode whitespace obfuscation"
        if self._contains_control_characters(command):
            return "Control characters"

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
