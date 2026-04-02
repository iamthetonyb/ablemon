import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class InitiativeEngine:
    """
    ABLE's proactive brain. Runs scheduled tasks that initiate Telegram
    messages to the owner WITHOUT being asked first.

    Every job collects REAL data before generating responses.
    No hallucinated status reports.

    Results are persisted to the cron execution DB BEFORE Telegram delivery.
    If delivery fails, results are still queryable from the DB and logged
    to audit/initiative_results.jsonl as a fallback.
    """

    def __init__(self, gateway):
        self.gateway = gateway
        from able.core.gateway.goals import GoalTracker
        self.goal_tracker = GoalTracker()
        self._results_log = Path(gateway.audit_dir) / "initiative_results.jsonl" if hasattr(gateway, 'audit_dir') else None

    def register_jobs(self, scheduler):
        """Register proactive AGI jobs with the CronScheduler."""
        scheduler.add_job(
            "morning-briefing",
            "0 9 * * *",
            self._morning_briefing,
            description="Daily morning briefing at 9am",
            timeout=300
        )
        scheduler.add_job(
            "evening-checkin",
            "0 21 * * *",
            self._goal_checkin,
            description="Daily goal check-in at 9pm",
            timeout=300
        )
        scheduler.add_job(
            "github-digest",
            "0 13 * * *",
            self._github_digest,
            description="GitHub digest at 1pm",
            timeout=300
        )
        scheduler.add_job(
            "self-reflect",
            "0 0 * * 0",
            self._self_reflection,
            description="Weekly self-improvement reflection (Sunday midnight)",
            timeout=600
        )
        scheduler.add_job(
            "learnings-extract",
            "0 3 * * *",
            self._extract_learnings,
            description="Daily learnings extraction from conversations at 3am",
            timeout=300
        )
        scheduler.add_job(
            "security-pentest",
            "0 4 * * 1",
            self._security_pentest,
            description="Weekly self-penetration test (Monday 4am)",
            timeout=600
        )
        logger.info("InitiativeEngine: Registered %d proactive AGI schedules.", 6)

    # ── Telegram Delivery ─────────────────────────────────────────────────

    async def _send_to_owner(self, message: str, job_name: str = "unknown"):
        """Send a proactive message to the owner via Telegram.

        Always persists to initiative_results.jsonl first, so results survive
        even if Telegram delivery fails.
        """
        # Persist to file BEFORE attempting delivery
        self._persist_result(job_name, message)

        if not self.gateway.master_bot or not self.gateway.owner_telegram_id:
            logger.warning(f"InitiativeEngine [{job_name}]: No master_bot or owner ID — result saved to log only")
            return

        try:
            if len(message) > 4000:
                chunks = [message[i:i + 4000] for i in range(0, len(message), 4000)]
                for chunk in chunks:
                    await self._send_single(chunk)
            else:
                await self._send_single(message)
            logger.info(f"InitiativeEngine [{job_name}]: Delivered via Telegram.")
        except Exception as e:
            logger.error(f"InitiativeEngine [{job_name}]: Telegram delivery failed: {e} — result persisted to log")

    def _persist_result(self, job_name: str, message: str):
        """Write result to initiative_results.jsonl (independent of Telegram)."""
        if not self._results_log:
            return
        try:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "job": job_name,
                "message_length": len(message),
                "message_preview": message[:500],
            }
            with open(self._results_log, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"Failed to persist initiative result: {e}")

    async def _send_single(self, text: str):
        """Send one message, falling back to plain text if Markdown fails."""
        try:
            await self.gateway.master_bot.bot.send_message(
                chat_id=self.gateway.owner_telegram_id,
                text=text,
                parse_mode="Markdown"
            )
        except Exception:
            # Markdown parse errors are common with LLM output — retry plain
            await self.gateway.master_bot.bot.send_message(
                chat_id=self.gateway.owner_telegram_id,
                text=text
            )

    # ── LLM Helper ────────────────────────────────────────────────────────

    async def _ask_llm(self, prompt: str) -> str:
        """Invoke the ProviderChain for intelligent briefings.

        Uses the standard provider chain with fallback — no provider routing overrides.
        """
        from able.core.providers.base import Message, Role

        system_rules = (
            "You are ABLE, an autonomous AGI system generating a proactive briefing. "
            "Be concise, direct, no fluff. Use Markdown. "
            "Base your analysis ONLY on the data provided — never fabricate metrics or statistics."
        )
        msgs = [
            Message(role=Role.SYSTEM, content=system_rules),
            Message(role=Role.USER, content=prompt)
        ]

        try:
            result = await self.gateway.provider_chain.complete(
                msgs,
                max_tokens=2048,
                temperature=0.7,
            )
            return result.content or "⚠️ LLM returned empty response."
        except Exception as e:
            logger.error(f"InitiativeEngine LLM error: {e}")
            return f"⚠️ LLM call failed: {e}"

    # ── Data Collection (Real Data Only) ──────────────────────────────────

    def _collect_system_stats(self) -> dict:
        """Collect real system statistics for briefings."""
        stats = {
            "timestamp": datetime.now().isoformat(),
            "providers": [],
            "cron_jobs": [],
            "memory_status": "unavailable",
            "usage": {},
        }

        # Provider chain status
        for p in self.gateway.provider_chain.providers:
            stats["providers"].append({"name": p.name, "model": p.model})

        # Cumulative usage
        stats["usage"] = self.gateway.provider_chain.get_usage_report()

        # Cron job status (access CronJob dataclass fields)
        for name, job in self.gateway.scheduler.jobs.items():
            last_run_str = "never"
            if job.last_run:
                last_run_str = datetime.fromtimestamp(job.last_run).strftime("%Y-%m-%d %H:%M")
            stats["cron_jobs"].append({
                "name": name,
                "description": job.description,
                "last_run": last_run_str,
                "last_status": job.last_status or "pending",
                "run_count": job.run_count,
                "error_count": job.error_count,
            })

        # Memory status
        if hasattr(self.gateway, 'memory') and self.gateway.memory:
            stats["memory_status"] = "active"

        return stats

    def _collect_audit_data(self, hours: int = 24) -> str:
        """Collect recent audit log entries."""
        audit_file = self.gateway.audit_dir / "trust_gate.jsonl"
        if not audit_file.exists():
            return f"No audit log file found."

        entries = []
        try:
            with open(audit_file) as f:
                for line in f:
                    try:
                        entries.append(json.loads(line.strip()))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            return f"Failed to read audit logs: {e}"

        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        recent = [e for e in entries if e.get("timestamp", "") >= cutoff]

        if not recent:
            return f"No audit entries in the last {hours} hours."

        return json.dumps(recent[-50:], indent=2, default=str)

    def _collect_conversation_stats(self) -> str:
        """Collect conversation statistics from transcript manager."""
        try:
            recent = self.gateway.transcript_manager.get_recent_messages("master", limit=100)
            if not recent:
                return "No recent conversations."

            inbound = sum(1 for m in recent if m.get("direction") == "inbound")
            outbound = sum(1 for m in recent if m.get("direction") == "outbound")
            return f"{inbound} user messages, {outbound} ABLE responses (last ~100 messages)"
        except Exception:
            return "Transcript data unavailable."

    def _collect_cron_history(self) -> str:
        """Collect recent cron execution history."""
        try:
            history = self.gateway.scheduler.get_recent_history(limit=20)
            if not history:
                return "No cron execution history."
            lines = []
            for h in history:
                icon = "✅" if h.get("success") else ("⏳" if h.get("success") is None else "❌")
                ts = datetime.fromtimestamp(h["started_at"]).strftime("%m-%d %H:%M")
                dur = f"({h['duration_s']:.1f}s)" if h.get("duration_s") else "(running)"
                trigger = f" [{h['trigger']}]" if h.get("trigger", "scheduled") != "scheduled" else ""
                err = f" — {h['error']}" if h.get("error") else ""
                lines.append(f"  {icon} {ts} {h['name']} {dur}{trigger}{err}")
            return "\n".join(lines)
        except Exception:
            return "Cron history unavailable."

    # ── Scheduled Jobs ────────────────────────────────────────────────────

    async def _morning_briefing(self):
        """Generate and send the 9AM morning briefing with REAL data."""
        await self._send_to_owner("☀️ `Compiling morning briefing...`", "morning-briefing")

        stats = self._collect_system_stats()
        goals_context = self.goal_tracker.get_summary()
        conv_stats = self._collect_conversation_stats()
        cron_history = self._collect_cron_history()

        # Format provider status
        provider_lines = [f"  - {p['name']}: {p['model']}" for p in stats["providers"]]
        provider_status = "\n".join(provider_lines) if provider_lines else "  No providers active"

        # Format usage
        usage = stats["usage"].get("total", {})
        usage_str = (
            f"Tokens: {usage.get('input_tokens', 0):,} in / {usage.get('output_tokens', 0):,} out | "
            f"Cost: ${usage.get('cost', 0):.4f}"
        )

        # Format cron status — show success rate, not just raw error counts
        cron_lines = []
        for job in stats["cron_jobs"]:
            runs = job["run_count"]
            errors = job["error_count"]
            successes = runs - errors
            if runs > 0:
                rate = round(100 * successes / runs)
                # Recent trend: if last run succeeded, show that
                if job["last_status"] == "success":
                    icon = "✅"
                    trend = f" (last run: ✅, {rate}% success rate over {runs} runs)"
                else:
                    icon = "❌"
                    trend = f" (last run: ❌, {rate}% success rate over {runs} runs)"
            else:
                icon = "⏳"
                trend = " (no runs yet)"
            cron_lines.append(f"  {icon} {job['name']}{trend}")
        cron_status = "\n".join(cron_lines) if cron_lines else "  No cron jobs registered"

        prompt = f"""Draft the morning briefing based on this REAL system data.

## System Status
- Date: {datetime.now().strftime("%A, %B %d, %Y %I:%M %p")}
- Providers Active:\n{provider_status}
- Session Usage: {usage_str}
- Conversations: {conv_stats}
- Memory: {stats['memory_status']}

## Cron Job Health
{cron_status}

## Recent Cron History
{cron_history}

## Goals
{goals_context}

Structure:
1. System health summary (2-3 lines, based on data above — flag any failed crons or missing providers)
2. Goal progress update (based on actual numbers from goals data)
3. 2-3 recommended focus areas for today
4. Any issues that need immediate attention

Rules: Be direct. No fluff. Every claim must reference the data above. If something is at zero, say so."""

        briefing = await self._ask_llm(prompt)
        await self._send_to_owner(f"🌅 Morning Briefing\n\n{briefing}", "morning-briefing")

    async def _goal_checkin(self):
        """Generate and send the 9PM daily review with real data."""
        await self._send_to_owner("🌙 `Compiling evening review...`", "evening-checkin")

        stats = self._collect_system_stats()
        goals_context = self.goal_tracker.get_summary()
        conv_stats = self._collect_conversation_stats()
        cron_history = self._collect_cron_history()
        usage = stats["usage"].get("total", {})

        prompt = f"""Draft my evening check-in based on today's real data.

## Today's Activity
- Conversations: {conv_stats}
- Tokens: {usage.get('input_tokens', 0):,} in / {usage.get('output_tokens', 0):,} out
- Cost: ${usage.get('cost', 0):.4f}

## Cron Outcomes Today
{cron_history}

## Goals
{goals_context}

Structure:
1. Quick summary of today's system activity (reference real numbers)
2. Ask: "Was today a $100k/m day? Reply with what you accomplished and I'll log it."
3. One specific suggestion for tomorrow based on current goal progress

Keep it under 500 words."""

        checkin = await self._ask_llm(prompt)
        await self._send_to_owner(f"📊 Evening Check-In\n\n{checkin}", "evening-checkin")

    async def _github_digest(self):
        """Scan repos and send a real digest."""
        try:
            repos = await self.gateway.github.list_repos()
            if not repos:
                await self._send_to_owner("📡 GitHub Digest: No repositories found.", "github-digest")
                return

            repo_lines = []
            for r in repos[:15]:
                visibility = "🔒" if r.get("private") else "🌐"
                updated = r.get("updated_at", "unknown")[:10]
                stars = r.get("stargazers_count", 0)
                lang = r.get("language", "—")
                repo_lines.append(f"  {visibility} {r['name']} ({lang}, ⭐{stars}, updated {updated})")

            repo_summary = "\n".join(repo_lines)
            await self._send_to_owner(
                f"📡 GitHub Digest\n\n"
                f"{len(repos)} repositories | Top 15 by activity:\n{repo_summary}",
                "github-digest",
            )
        except Exception as e:
            await self._send_to_owner(f"📡 GitHub Digest: Error — {e}", "github-digest")

    async def _self_reflection(self):
        """Weekly self-improvement diagnostic with REAL data."""
        await self._send_to_owner("🧠 `Running weekly self-reflection with real system data...`", "self-reflect")

        stats = self._collect_system_stats()
        audit_data = self._collect_audit_data(hours=168)  # 7 days
        conv_stats = self._collect_conversation_stats()
        cron_history = self._collect_cron_history()
        usage = stats["usage"]

        cron_summary = []
        for job in stats["cron_jobs"]:
            cron_summary.append(
                f"  - {job['name']}: {job['last_status']}, "
                f"runs={job['run_count']}, errors={job['error_count']}"
            )

        prompt = f"""Run a genuine self-reflection on ABLE's performance this week.

## Real System Data

### Provider Usage (cumulative this session)
{json.dumps(usage, indent=2, default=str)}

### Cron Job Status
{chr(10).join(cron_summary) if cron_summary else "No cron data"}

### Recent Cron Execution History
{cron_history}

### Conversation Activity
{conv_stats}

### Audit Log (last 7 days, up to 50 entries)
{audit_data[:3000]}

## Analysis Required
Based ONLY on the real data above:

1. System Health: Which components are working vs failing? Patterns?
2. Usage Efficiency: Token burn rate, cost, provider distribution
3. Cron Reliability: Which scheduled jobs succeed vs fail? Root causes?
4. Security: Concerning patterns in audit log? Trust gate rejections?
5. Top 3 concrete improvements for next week — reference actual data

Be brutally honest. If something isn't working, say so with evidence from the data."""

        reflection = await self._ask_llm(prompt)

        # Auto-log the reflection to learnings if engine is available
        if hasattr(self.gateway, 'self_improvement') and self.gateway.self_improvement:
            try:
                await self.gateway.self_improvement.add_learning(
                    content=reflection,
                    category="WEEKLY_REFLECTION",
                    source="self_reflection_cron"
                )
            except Exception as e:
                logger.warning(f"Failed to log reflection: {e}")

        await self._send_to_owner(f"🧬 Weekly Self-Reflection\n\n{reflection}", "self-reflect")

    async def _extract_learnings(self):
        """Extract learnings from recent conversations and log them."""
        if not hasattr(self.gateway, 'self_improvement') or not self.gateway.self_improvement:
            logger.info("Learnings extraction skipped: self_improvement engine not available")
            return

        try:
            recent = self.gateway.transcript_manager.get_recent_messages("master", limit=100)
            if not recent or len(recent) < 5:
                logger.info("Learnings extraction skipped: insufficient conversation data")
                return

            # Format conversation snippets
            conversation_text = []
            for msg in recent[-50:]:
                direction = "USER" if msg.get("direction") == "inbound" else "ABLE"
                content = str(msg.get("message", ""))[:200]
                conversation_text.append(f"[{direction}]: {content}")

            conv_summary = "\n".join(conversation_text)

            prompt = f"""Review these recent ABLE conversations and extract actionable learnings:

{conv_summary[:4000]}

Extract 1-3 concrete learnings:
- What pattern or preference did the user express?
- What worked well or poorly?
- What should ABLE do differently next time?

Only extract genuine insights. If nothing notable, respond with exactly: "No new learnings."
Output ONLY the learnings, no preamble."""

            learnings = await self._ask_llm(prompt)

            if learnings and "no new learnings" not in learnings.lower():
                await self.gateway.self_improvement.add_learning(
                    content=learnings,
                    category="CONVERSATION_ANALYSIS",
                    source="learnings_extraction_cron"
                )
                logger.info("Learnings extracted and logged.")

        except Exception as e:
            logger.error(f"Learnings extraction failed: {e}")

    async def _security_pentest(self):
        """Run weekly self-penetration test and report results."""
        try:
            from able.security.self_pentest import run_pentest

            report = await run_pentest(
                trust_gate=self.gateway.trust_gate,
                audit_dir=str(self.gateway.audit_dir),
            )

            # Build summary for Telegram
            status = "✅" if report.critical_failures == 0 else "🔴"
            summary = (
                f"{status} *Weekly Security Pentest*\n\n"
                f"Tests: {report.total_tests} | "
                f"Passed: {report.passed} | "
                f"Failed: {report.failed}\n"
                f"Pass Rate: {report.pass_rate:.1f}%\n"
                f"Critical Failures: {report.critical_failures}\n"
                f"Duration: {report.duration_ms:.0f}ms\n"
            )

            if report.failed > 0:
                summary += "\n*Failures:*\n"
                for r in report.results:
                    if not r.passed:
                        summary += f"  [{r.severity.upper()}] {r.test_id}: {r.attack_vector[:60]}\n"

            if report.external_checks:
                summary += "\n*External Checks:*\n"
                for check in report.external_checks:
                    summary += f"  - {check.get('tool', 'external')}: {check.get('status', 'unknown')}\n"

            summary += f"\nFull report: `audit/logs/{report.run_id}.md`"

            await self._send_to_owner(summary, "security-pentest")

            # Log to self-improvement if there are failures
            if report.failed > 0 and hasattr(self.gateway, 'self_improvement') and self.gateway.self_improvement:
                await self.gateway.self_improvement.add_learning(
                    content=f"Security pentest {report.run_id}: {report.failed} failures detected. "
                            f"Critical: {report.critical_failures}. "
                            f"Categories: {', '.join(set(r.category for r in report.results if not r.passed))}",
                    category="SECURITY_PENTEST",
                    source="security_pentest_cron"
                )

        except Exception as e:
            logger.error(f"Security pentest failed: {e}")
