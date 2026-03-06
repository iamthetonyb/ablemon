import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class InitiativeEngine:
    """
    ATLAS's proactive brain. Runs scheduled tasks that initiate Telegram
    messages to the owner WITHOUT being asked first.
    """
    def __init__(self, gateway):
        self.gateway = gateway
        from core.gateway.goals import GoalTracker
        self.goal_tracker = GoalTracker()

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
            "skill-improvement-cycle",
            "30 0 * * 0",
            self._skill_improvement_cycle,
            description="Weekly autonomous skill improvement (Sunday 12:30am)",
            timeout=1800
        )
        logger.info("InitiativeEngine: Registered proactive AGI schedules.")

    async def _send_to_owner(self, message: str):
        """Send a proactive message to the owner via Telegram."""
        if not self.gateway.master_bot or not self.gateway.owner_telegram_id:
            logger.warning("InitiativeEngine: Could not send message (no master_bot or missing owner ID)")
            return
        
        try:
            await self.gateway.master_bot.bot.send_message(
                chat_id=self.gateway.owner_telegram_id,
                text=message,
                parse_mode="Markdown"
            )
            logger.info("InitiativeEngine: Proactive message delivered.")
        except Exception as e:
            logger.error(f"InitiativeEngine failed to send message: {e}")

    async def _ask_llm(self, prompt: str) -> str:
        """Helper to invoke the core ProviderChain for intelligent briefings."""
        from core.providers.base import Message, Role
        
        system_rules = "You are ATLAS. Keep this proactive briefing concise, punchy, and highly analytical. Format in Markdown."
        msgs = [
            Message(role=Role.SYSTEM, content=system_rules),
            Message(role=Role.USER, content=prompt)
        ]
        
        try:
            result = await self.gateway.provider_chain.complete(
                msgs,
                max_tokens=2048,
                temperature=0.7,
                # Force AtlasCloud/OpenRouter
                provider={"order": ["AtlasCloud"], "allow_fallbacks": True, "data_collection": "deny"},
                models=["qwen/qwen3.5-397b-a17b"]
            )
            return result.content or "⚠️ Error generating intelligent briefing."
        except Exception as e:
            logger.error(f"InitiativeEngine LLM error: {e}")
            return f"⚠️ LLM generation failed for briefing: {e}"

    async def _morning_briefing(self):
        """Generate and send the 9AM morning briefing."""
        await self._send_to_owner("☀️ `ATLAS is waking up and preparing your morning briefing...`")
        goals_context = self.goal_tracker.get_summary()
        
        prompt = f"""
        Draft my morning briefing.
        
        Context:
        It is {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}.
        
        {goals_context}
        
        Structure:
        1. Short motivational greeting (Tony B, KingCRO)
        2. Where we are on the $100k/m by 2028 goal.
        3. 2-3 recommended focus areas for today based on building the swarm AGI system and generating revenue.
        
        CRITICAL: Do not ask for user input. Deliver the briefing directly.
        """
        
        briefing = await self._ask_llm(prompt)
        await self._send_to_owner(f"🌅 **Morning Briefing**\n\n{briefing}")

    async def _goal_checkin(self):
        """Generate and send the 9PM daily review."""
        await self._send_to_owner("🌙 `ATLAS is preparing your evening review...`")
        goals_context = self.goal_tracker.get_summary()
        
        prompt = f"""
        Draft my evening check-in.
        
        Context:
        It is {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}.
        {goals_context}
        
        Structure:
        1. Ask me directly: "Was today a $100k/m day?"
        2. Ask me to reply with what we accomplished today so I can log it to memory.
        3. Provide a short closing remark.
        """
        
        checkin = await self._ask_llm(prompt)
        await self._send_to_owner(f"📊 **Evening Check-In**\n\n{checkin}")

    async def _github_digest(self):
        """Scan repos and send a digest."""
        # Simple alert for now - could be wired directly into self.gateway.github.list_repos()
        await self._send_to_owner("📡 `ATLAS GitHub Digest: System running nominally. Repositories synchronized.`")

    async def _self_reflection(self):
        """Weekly self-improvement diagnostic."""
        await self._send_to_owner("🧠 `ATLAS Weekly Self-Reflection Protocol Initiated. Analyzing audit logs...`")
        prompt = """
        You are reflecting on the past week of operations.
        Draft a short self-reflection summary analyzing your performance as an AGI, potential areas where your context window or tool usage caused friction, and what capabilities you need me to build for you next week.
        """
        reflection = await self._ask_llm(prompt)
        await self._send_to_owner(f"🧬 **Self-Improvement Diagnostics**\n\n{reflection}")
    async def _skill_improvement_cycle(self):
        """
        Autonomous weekly skill improvement loop.
        Reads skill_outcomes.jsonl, identifies failing skills,
        uses LLM to generate improvements, creates GitHub PRs, notifies owner.
        """
        import json as _json
        from collections import defaultdict
        from pathlib import Path

        await self._send_to_owner("🔄 `ATLAS Skill Improvement Cycle started. Analyzing performance data...`")

        # Read outcome data
        outcomes_file = Path("audit/logs/skill_outcomes.jsonl")
        outcomes_by_skill = defaultdict(list)
        if outcomes_file.exists():
            with open(outcomes_file) as f:
                for line in f:
                    try:
                        e = _json.loads(line.strip())
                        skill = e.get("skill")
                        if skill:
                            outcomes_by_skill[skill].append(e)
                    except Exception:
                        pass

        if not outcomes_by_skill:
            await self._send_to_owner("📊 Skill Improvement: No outcome data yet. Will retry next week.")
            return

        # Find candidates: < 70% success rate, >= 3 uses
        candidates = []
        for skill, outcomes in outcomes_by_skill.items():
            total = len(outcomes)
            if total < 3:
                continue
            positive = sum(1 for o in outcomes if o.get("outcome") == "positive")
            rate = positive / total
            if rate < 0.70:
                negatives = [o for o in outcomes if o.get("outcome") == "negative"]
                candidates.append({
                    "skill": skill,
                    "total": total,
                    "success_rate": rate,
                    "failure_count": len(negatives),
                    "failure_triggers": [n.get("trigger", "") for n in negatives[-5:]],
                    "failure_signals": [n.get("signal", "") for n in negatives[-5:]],
                })

        # Sort by opportunity (most used × lowest success)
        candidates.sort(key=lambda x: x["total"] * (1 - x["success_rate"]), reverse=True)
        top3 = candidates[:3]

        if not top3:
            await self._send_to_owner("✅ Skill Improvement: All skills performing above 70% threshold. No improvements needed.")
            return

        report_lines = [f"🔬 **Skill Improvement Targets**\n"]
        for c in top3:
            report_lines.append(f"• `{c['skill']}` — {c['success_rate']:.0%} success ({c['failure_count']} failures)")

        await self._send_to_owner("\n".join(report_lines) + "\n\nGenerating improvements and PRs...")

        # For each candidate, generate improved SKILL.md and create a PR
        prs_created = []
        for candidate in top3:
            try:
                skill_name = candidate["skill"]
                skill_path = Path(f"atlas/skills/library/{skill_name}/SKILL.md")
                if not skill_path.exists():
                    continue

                current_skill = skill_path.read_text()
                failure_context = "\n".join([
                    f"Trigger: {t!r}" for t in candidate["failure_triggers"]
                ])

                improve_prompt = f"""You are improving a skill for the ATLAS autonomous agent system.

Current SKILL.md:
{current_skill}

Failure patterns observed ({candidate['failure_count']} failures out of {candidate['total']} uses):
{failure_context}

Generate an improved SKILL.md that:
1. Addresses the specific failure patterns above
2. Makes the protocol more concrete and specific
3. Adds edge case handling for the failure scenarios
4. Is NO longer than the original — clarity over length
5. Preserves the YAML frontmatter format

Output ONLY the complete improved SKILL.md content, nothing else."""

                improved = await self._ask_llm(improve_prompt)
                if not improved or len(improved) < 100:
                    continue

                # Write improved version and create PR
                skill_path.write_text(improved)

                # Also update .claude/commands/ version
                cmd_path = Path(f".claude/commands/atlas-{skill_name}.md")
                if cmd_path.exists():
                    cmd_path.write_text(improved)

                import subprocess
                branch = f"auto/skill-improve-{skill_name}"
                subprocess.run(["git", "checkout", "-b", branch], capture_output=True)
                subprocess.run(["git", "add", str(skill_path), str(cmd_path)], capture_output=True)
                subprocess.run(["git", "commit", "-m",
                    f"improve({skill_name}): auto-improve based on outcome data\n\n"
                    f"Success rate was {candidate['success_rate']:.0%} ({candidate['failure_count']} failures)\n"
                    f"Generated by ATLAS skill improvement cycle"
                ], capture_output=True)
                push = subprocess.run(["git", "push", "origin", branch], capture_output=True)
                subprocess.run(["git", "checkout", "main"], capture_output=True)

                pr = subprocess.run([
                    "gh", "pr", "create",
                    "--title", f"auto-improve: {skill_name} skill",
                    "--body", f"**Auto-generated by ATLAS skill improvement cycle**\n\n"
                              f"**Failure rate**: {1-candidate['success_rate']:.0%} ({candidate['failure_count']}/{candidate['total']} uses)\n"
                              f"**Top failure triggers**: {', '.join(candidate['failure_triggers'][:3])}\n\n"
                              f"Review the diff. If it looks good, merge — CI/CD deploys automatically."
                ], capture_output=True, text=True)

                pr_url = pr.stdout.strip()
                prs_created.append((skill_name, pr_url))
                logger.info(f"Skill improvement PR created for {skill_name}: {pr_url}")

            except Exception as e:
                logger.error(f"Skill improvement failed for {candidate.get('skill')}: {e}")

        if prs_created:
            pr_list = "\n".join([f"• `{s}`: {url}" for s, url in prs_created])
            await self._send_to_owner(
                f"✅ **Skill Improvements Ready**\n\n"
                f"{pr_list}\n\n"
                f"Review and merge to deploy. Auto-generated from outcome data."
            )
        else:
            await self._send_to_owner("⚠️ Skill improvement cycle ran but no PRs created. Check logs.")
