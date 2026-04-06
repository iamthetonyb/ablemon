"""
Batch Trajectory Generator — synthetically grow the distillation corpus.

Runs ABLE's own routing pipeline against a prompt dataset and captures each
(prompt, response) pair as a HarvestedConversation in ChatML format.  These
feed directly into the DPO builder, taking the corpus from ~20 pairs to
hundreds in a single run.

Architecture:
  1. Load prompts from JSONL/TXT file, inline list, or Codex dash output
  2. Score each prompt through ComplexityScorer (same as live routing)
  3. Route to the appropriate provider tier (T1/T2/T4/T5)
  4. Capture (prompt, thinking_if_any, response) as HarvestedConversation
  5. Write to ChatML JSONL; feed DPO builder for chosen/rejected pairs

Checkpoint support: saves progress after each batch so runs can be resumed
after a crash or rate-limit pause.

Usage (CLI):
    # Basic — prompts from JSONL, output to distillation dir
    python -m able.core.distillation.batch_runner \\
        --prompts data/prompts.jsonl \\
        --output  data/batch_trajectories.jsonl \\
        --concurrent 3

    # Codex mode — pulls tasks from Codex MCP dash command
    python -m able.core.distillation.batch_runner --codex --output data/codex_pairs.jsonl

    # Dry run — score + tier assignment only, no LLM calls
    python -m able.core.distillation.batch_runner --prompts data/prompts.jsonl --dry-run

Prompt JSONL format (one per line):
    {"prompt": "Explain async/await in Python", "domain": "coding"}
    {"prompt": "Write a security audit checklist for a REST API", "domain": "security"}
    {"prompt": "Draft a cold outreach email for a SaaS product"}  # domain auto-detected

Plain text format: one prompt per line (domain auto-detected from keywords).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are Able, an expert AI assistant. "
    "Provide clear, direct, high-quality responses. "
    "When given a coding task, produce correct, idiomatic code with brief explanation. "
    "When given a writing task, be direct and professional. "
    "Never start with sycophantic openers."
)

_DEFAULT_OUTPUT = "data/batch_trajectories.jsonl"
_DEFAULT_CHECKPOINT = "data/batch_runner_checkpoint.json"


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class PromptTask:
    """A single prompt task with routing metadata."""
    idx: int
    prompt: str
    domain: str = "default"
    expected_tier: Optional[int] = None
    complexity_score: Optional[float] = None
    source: str = "batch"


@dataclass
class Trajectory:
    """A captured (prompt → response) trajectory."""
    task: PromptTask
    response: str
    thinking: str
    provider: str
    model: str
    tier: int
    complexity_score: float
    latency_ms: float
    input_tokens: int
    output_tokens: int
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.response)

    def to_harvested_conversation(self) -> Any:
        """Convert to HarvestedConversation for the formatter / DPO builder."""
        from able.core.distillation.harvesters.base import HarvestedConversation

        messages = [
            {"role": "user", "content": self.task.prompt},
            {"role": "assistant", "content": self.response},
        ]
        return HarvestedConversation(
            id=f"batch_{self.task.idx}_{uuid.uuid4().hex[:8]}",
            source="batch_trajectory",
            messages=messages,
            model=self.model,
            timestamp=datetime.fromisoformat(self.timestamp),
            domain=self.task.domain,
            thinking_blocks=[self.thinking] if self.thinking else [],
            metadata={
                "tier": self.tier,
                "complexity_score": self.complexity_score,
                "provider": self.provider,
                "latency_ms": self.latency_ms,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "source": self.task.source,
            },
        )


@dataclass
class BatchResult:
    total: int
    successful: int
    failed: int
    skipped: int
    trajectories: List[Trajectory] = field(default_factory=list)
    output_path: str = _DEFAULT_OUTPUT
    checkpoint_path: str = _DEFAULT_CHECKPOINT
    elapsed_seconds: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successful / self.total if self.total else 0.0

    def summary(self) -> str:
        return (
            f"Batch complete: {self.successful}/{self.total} successful "
            f"({self.success_rate:.0%}), {self.failed} failed, "
            f"{self.skipped} skipped — {self.elapsed_seconds:.1f}s "
            f"→ {self.output_path}"
        )


# ── Core runner ───────────────────────────────────────────────────────────────

class BatchTrajectoryRunner:
    """
    Run ABLE's routing pipeline against a prompt dataset and capture trajectories.

    Args:
        output_path:     JSONL file to write trajectories to.
        checkpoint_path: JSON file to save/resume progress.
        system_prompt:   System prompt injected into every provider call.
        config_path:     Path to routing_config.yaml (default: auto-detect).
        dry_run:         Score + tier assignment only — no LLM calls.
    """

    def __init__(
        self,
        output_path: str = _DEFAULT_OUTPUT,
        checkpoint_path: str = _DEFAULT_CHECKPOINT,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
        config_path: Optional[str] = None,
        dry_run: bool = False,
    ) -> None:
        self._output_path = output_path
        self._checkpoint_path = checkpoint_path
        self._system_prompt = system_prompt
        self._config_path = config_path
        self._dry_run = dry_run
        self._completed_idxs: set[int] = set()

        # Ensure output dir exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Load checkpoint if it exists
        self._load_checkpoint()

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(
        self,
        prompts: List[PromptTask] | List[str] | str,
        max_concurrent: int = 3,
    ) -> BatchResult:
        """
        Run the batch.

        Args:
            prompts:        List of PromptTask/str, or path to a JSONL/TXT file.
            max_concurrent: Max parallel provider calls (respect rate limits).
        """
        tasks = self._normalise_prompts(prompts)
        logger.info(
            "BatchRunner: %d tasks, %d already complete, concurrent=%d, dry_run=%s",
            len(tasks),
            len(self._completed_idxs),
            max_concurrent,
            self._dry_run,
        )

        pending = [t for t in tasks if t.idx not in self._completed_idxs]
        if not pending:
            logger.info("All tasks already complete (checkpoint)")

        t_start = time.time()
        trajectories: List[Trajectory] = []
        failed = 0
        skipped = len(tasks) - len(pending)

        # Score all pending tasks (cheap, synchronous)
        for task in pending:
            task.complexity_score, task.domain = self._score(task.prompt, task.domain)

        if self._dry_run:
            logger.info("Dry run — printing tier assignments, no LLM calls")
            for task in pending:
                tier = self._tier_from_score(task.complexity_score)
                print(f"[{task.idx:4d}] score={task.complexity_score:.2f} tier={tier} domain={task.domain}  {task.prompt[:80]}")
            return BatchResult(
                total=len(tasks), successful=0, failed=0, skipped=len(tasks),
                output_path=self._output_path,
                checkpoint_path=self._checkpoint_path,
                elapsed_seconds=time.time() - t_start,
            )

        # Execute in batches of max_concurrent
        sem = asyncio.Semaphore(max_concurrent)

        async def _bounded(task: PromptTask) -> Optional[Trajectory]:
            async with sem:
                return await self._run_single(task)

        results = await asyncio.gather(*[_bounded(t) for t in pending])

        for traj in results:
            if traj is None:
                failed += 1
            elif traj.ok:
                trajectories.append(traj)
                self._write_trajectory(traj)
                self._completed_idxs.add(traj.task.idx)
                self._save_checkpoint()
            else:
                failed += 1
                logger.warning(
                    "Task %d failed: %s", traj.task.idx, traj.error
                )

        elapsed = time.time() - t_start
        result = BatchResult(
            total=len(tasks),
            successful=len(trajectories),
            failed=failed,
            skipped=skipped,
            trajectories=trajectories,
            output_path=self._output_path,
            checkpoint_path=self._checkpoint_path,
            elapsed_seconds=elapsed,
        )
        logger.info(result.summary())
        return result

    async def run_codex_tasks(
        self,
        max_tasks: int = 50,
        max_concurrent: int = 2,
        boost_domains: Optional[List[str]] = None,
    ) -> BatchResult:
        """
        Pull coding tasks from Codex MCP (`codex dash`) and run them through
        ABLE's pipeline to generate coding-domain trajectories.

        boost_domains: domains to over-represent (2x prompts) — used by the
        weak-domain auto-targeting loop so deficient areas get more training data.

        Requires the Codex MCP tool to be configured.  Falls back to a built-in
        set of coding prompts when Codex is unavailable.
        """
        tasks = await self._load_codex_tasks(max_tasks, boost_domains=boost_domains or [])
        logger.info("Loaded %d Codex tasks (boost_domains=%s)", len(tasks), boost_domains)
        return await self.run(tasks, max_concurrent=max_concurrent)

    def export_chatml(self, conversations: Optional[List[Any]] = None) -> int:
        """
        Export all collected trajectories as ChatML JSONL.

        If conversations is not passed, reads from the output_path file.
        Returns the number of pairs written.
        """
        if conversations is None:
            conversations = self._load_trajectories_from_disk()

        out_path = Path(self._output_path).with_suffix(".chatml.jsonl")
        count = 0
        with open(out_path, "w") as fh:
            for conv in conversations:
                if hasattr(conv, "to_harvested_conversation"):
                    hc = conv.to_harvested_conversation()
                else:
                    hc = conv

                record = {
                    "messages": hc.messages,
                    "model": hc.model,
                    "domain": hc.domain,
                    "metadata": hc.metadata,
                }
                fh.write(json.dumps(record) + "\n")
                count += 1

        logger.info("Exported %d ChatML records → %s", count, out_path)
        return count

    # ── Core execution ─────────────────────────────────────────────────────────

    async def _run_single(self, task: PromptTask) -> Optional[Trajectory]:
        """Run a single prompt through ABLE's routing pipeline."""
        tier = self._tier_from_score(task.complexity_score or 0.0)
        provider_name, provider = self._get_provider(tier)

        if provider is None:
            logger.warning(
                "Task %d: no provider available for tier %d — skipping",
                task.idx,
                tier,
            )
            return Trajectory(
                task=task, response="", thinking="",
                provider="none", model="none",
                tier=tier, complexity_score=task.complexity_score or 0.0,
                latency_ms=0, input_tokens=0, output_tokens=0,
                error=f"No provider for tier {tier}",
            )

        from able.core.providers.base import Message, Role

        messages = [
            Message(role=Role.USER, content=task.prompt),
        ]

        t0 = time.time()
        try:
            result = await provider.complete(
                messages=messages,
                system=self._system_prompt,
                max_tokens=2048,
            )
            latency_ms = (time.time() - t0) * 1000

            # Extract thinking if present
            thinking = result.thinking_content or ""
            response = result.content or ""

            logger.info(
                "[%d/%s] tier=%d %s → %d chars, %.0fms",
                task.idx,
                task.domain[:8],
                tier,
                result.model,
                len(response),
                latency_ms,
            )

            return Trajectory(
                task=task,
                response=response,
                thinking=thinking,
                provider=result.provider,
                model=result.model,
                tier=tier,
                complexity_score=task.complexity_score or 0.0,
                latency_ms=latency_ms,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
            )

        except Exception as exc:
            latency_ms = (time.time() - t0) * 1000
            logger.warning("Task %d failed (%.0fms): %s", task.idx, latency_ms, exc)
            return Trajectory(
                task=task, response="", thinking="",
                provider=provider_name, model="unknown",
                tier=tier, complexity_score=task.complexity_score or 0.0,
                latency_ms=latency_ms, input_tokens=0, output_tokens=0,
                error=str(exc),
            )

    # ── Routing helpers ───────────────────────────────────────────────────────

    def _score(self, prompt: str, domain_hint: str = "") -> Tuple[float, str]:
        """Score a prompt using ABLE's ComplexityScorer."""
        try:
            from able.core.routing.scorer import ComplexityScorer
            scorer = ComplexityScorer()
            score_result = scorer.score(prompt)
            domain = domain_hint or getattr(score_result, "domain", "default")
            complexity = getattr(score_result, "score", 0.3)
            return float(complexity), str(domain)
        except Exception as exc:
            logger.debug("Scorer unavailable (%s) — using heuristic", exc)
            # Simple heuristic fallback
            length_score = min(len(prompt) / 2000, 0.4)
            code_keywords = ("def ", "class ", "import ", "async ", "docker", "deploy")
            security_keywords = ("security", "vulnerability", "CVE", "exploit", "audit")
            score = length_score
            domain = domain_hint or "default"
            for kw in code_keywords:
                if kw in prompt:
                    score += 0.15
                    domain = domain or "coding"
                    break
            for kw in security_keywords:
                if kw.lower() in prompt.lower():
                    score += 0.25
                    domain = domain or "security"
                    break
            return min(score, 1.0), domain

    def _tier_from_score(self, score: float) -> int:
        """Map complexity score to tier (matches routing_config.yaml thresholds)."""
        if score < 0.4:
            return 1
        elif score < 0.7:
            return 2
        else:
            return 4

    def _get_provider(self, tier: int) -> Tuple[str, Any]:
        """
        Return (provider_name, provider_instance) for the given tier.

        Tries to load from routing_config.yaml. Falls back to direct provider
        construction from env vars when config is unavailable.
        """
        try:
            return self._get_provider_from_config(tier)
        except Exception as exc:
            logger.debug("Config-based provider load failed (%s) — using env fallback", exc)
            return self._get_provider_from_env(tier)

    def _get_provider_from_config(self, tier: int) -> Tuple[str, Any]:
        """Load provider using the routing config (matches live routing exactly)."""
        config_path = self._config_path
        if config_path is None:
            # Auto-detect: walk up from CWD
            for candidate in [
                Path("config/routing_config.yaml"),
                Path(__file__).parent.parent.parent.parent / "config" / "routing_config.yaml",
            ]:
                if candidate.exists():
                    config_path = str(candidate)
                    break

        if config_path is None:
            raise FileNotFoundError("routing_config.yaml not found")

        import yaml  # type: ignore[import-untyped]

        with open(config_path) as fh:
            config = yaml.safe_load(fh)

        providers = config.get("providers", [])
        candidates = [p for p in providers if p.get("tier") == tier and p.get("enabled", True)]

        if not candidates:
            raise ValueError(f"No enabled providers for tier {tier}")

        # Pick primary (first in list for this tier)
        pc = candidates[0]
        provider_type = pc.get("provider_type", "")
        name = pc.get("name", "unknown")

        provider = self._build_provider(pc, provider_type)
        return name, provider

    def _get_provider_from_env(self, tier: int) -> Tuple[str, Any]:
        """Fallback: construct provider from environment variables."""
        if tier <= 2:
            # Try OpenRouter first (most universally available)
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if api_key:
                from able.core.providers.openrouter import OpenRouterProvider
                return "openrouter-fallback", OpenRouterProvider(
                    api_key=api_key,
                    model="google/gemma-4-31b-it",
                    base_url="https://openrouter.ai/api/v1",
                )
        # T4 — Anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            from able.core.providers.anthropic_provider import AnthropicProvider
            return "anthropic-fallback", AnthropicProvider(
                api_key=api_key, model="claude-opus-4-6"
            )
        # T5 — Ollama (free, offline)
        from able.core.providers.ollama import OllamaProvider
        return "ollama-fallback", OllamaProvider(model="gemma4:31b-cloud")

    def _build_provider(self, config: Dict[str, Any], provider_type: str) -> Any:
        """Construct a provider instance from a routing_config.yaml provider entry."""
        api_key = ""
        api_key_env = config.get("api_key_env", "")
        if api_key_env:
            api_key = os.environ.get(api_key_env, "")

        model_id = config.get("model_id", "")
        endpoint = config.get("endpoint", "")
        extra = config.get("extra", {})

        if provider_type == "openrouter":
            from able.core.providers.openrouter import OpenRouterProvider
            return OpenRouterProvider(
                api_key=api_key,
                model=model_id,
                base_url=endpoint or "https://openrouter.ai/api/v1",
            )
        elif provider_type == "anthropic":
            from able.core.providers.anthropic_provider import AnthropicProvider
            return AnthropicProvider(api_key=api_key, model=model_id)
        elif provider_type == "nvidia_nim":
            from able.core.providers.nvidia_nim import NVIDIANIMProvider
            return NVIDIANIMProvider(api_key=api_key, model=model_id)
        elif provider_type == "ollama":
            from able.core.providers.ollama import OllamaProvider
            return OllamaProvider(model=model_id)
        elif provider_type in ("openai_oauth", "openai"):
            # OAuth PKCE requires browser auth — not available in batch mode.
            # Fall back to direct OpenAI API if OPENAI_API_KEY is set.
            openai_key = os.environ.get("OPENAI_API_KEY")
            if openai_key:
                from openai import AsyncOpenAI
                # Wrap in a minimal adapter
                return _OpenAIAdapter(
                    api_key=openai_key,
                    model=model_id or "gpt-4o",
                )
            raise ValueError(f"openai_oauth provider requires OPENAI_API_KEY for batch mode")
        else:
            raise ValueError(f"Unknown provider_type: {provider_type}")

    # ── Codex integration ─────────────────────────────────────────────────────

    async def _load_codex_tasks(
        self,
        max_tasks: int,
        boost_domains: Optional[List[str]] = None,
    ) -> List[PromptTask]:
        """
        Load coding tasks from Codex MCP or fall back to the built-in set.

        boost_domains: domains to add a second copy of (weak-domain auto-targeting).
        Boosted domains get 2x representation in the task list so the model
        trains more on its weakest areas.
        """
        tasks: List[PromptTask] = []

        # Try Codex MCP first
        try:
            tasks = await self._fetch_codex_mcp_tasks(max_tasks)
            if tasks:
                logger.info("Loaded %d tasks from Codex MCP", len(tasks))
                # Still apply boost even for MCP tasks
                if boost_domains:
                    tasks = self._apply_domain_boost(tasks, boost_domains)
                return tasks
        except Exception as exc:
            logger.info("Codex MCP unavailable (%s) — using built-in coding prompts", exc)

        # Built-in set — start with standard allocation
        built_in = list(_BUILT_IN_CODING_PROMPTS)

        # Boost weak domains: pull all prompts for those domains to the front,
        # then append a second copy so they appear twice in the task list.
        if boost_domains:
            built_in = self._apply_domain_boost_entries(built_in, boost_domains)

        idx = 0
        for entry in built_in[:max_tasks]:
            tasks.append(PromptTask(
                idx=idx,
                prompt=entry["prompt"],
                domain=entry.get("domain", "coding"),
                source=entry.get("source", "codex_builtin"),
            ))
            idx += 1
        return tasks

    def _apply_domain_boost(
        self, tasks: List[PromptTask], boost_domains: List[str]
    ) -> List[PromptTask]:
        """Duplicate tasks in weak domains and append them."""
        boosted = list(tasks)
        extras = [t for t in tasks if t.domain in boost_domains]
        max_idx = max((t.idx for t in tasks), default=0)
        for i, t in enumerate(extras):
            copy = PromptTask(
                idx=max_idx + i + 1,
                prompt=t.prompt,
                domain=t.domain,
                source=t.source + "_boost",
            )
            boosted.append(copy)
        return boosted

    def _apply_domain_boost_entries(
        self, entries: List[Dict[str, str]], boost_domains: List[str]
    ) -> List[Dict[str, str]]:
        """Duplicate built-in entries in weak domains."""
        boosted = list(entries)
        for entry in entries:
            if entry.get("domain") in boost_domains:
                boosted.append({**entry, "source": entry.get("source", "codex_builtin") + "_boost"})
        return boosted

    async def _fetch_codex_mcp_tasks(self, max_tasks: int) -> List[PromptTask]:
        """Fetch tasks from the Codex MCP `dash` command."""
        # The Codex MCP tool returns a list of pending tasks/issues.
        # We convert each to a coding prompt.
        # This requires the codex MCP to be configured in .mcp.json.
        raise NotImplementedError("Codex MCP fetch not yet implemented — using built-in")

    # ── I/O helpers ───────────────────────────────────────────────────────────

    def _normalise_prompts(self, prompts: Any) -> List[PromptTask]:
        """Accept List[str], List[PromptTask], or a file path string."""
        if isinstance(prompts, str):
            return self._load_prompt_file(prompts)
        if not prompts:
            return []
        result: List[PromptTask] = []
        for i, p in enumerate(prompts):
            if isinstance(p, PromptTask):
                p.idx = i
                result.append(p)
            elif isinstance(p, dict):
                result.append(PromptTask(
                    idx=i,
                    prompt=p.get("prompt", ""),
                    domain=p.get("domain", ""),
                    expected_tier=p.get("expected_tier"),
                    source=p.get("source", "batch"),
                ))
            else:
                result.append(PromptTask(idx=i, prompt=str(p)))
        return result

    def _load_prompt_file(self, path: str) -> List[PromptTask]:
        """Load prompts from JSONL or plain text file."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")

        tasks: List[PromptTask] = []
        with open(p) as fh:
            for i, line in enumerate(fh):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("{"):
                    # JSONL
                    try:
                        entry = json.loads(line)
                        tasks.append(PromptTask(
                            idx=i,
                            prompt=entry.get("prompt", ""),
                            domain=entry.get("domain", ""),
                            expected_tier=entry.get("expected_tier"),
                            source=entry.get("source", path),
                        ))
                    except json.JSONDecodeError as exc:
                        logger.warning("Line %d: invalid JSON (%s) — skipping", i, exc)
                else:
                    # Plain text
                    tasks.append(PromptTask(idx=i, prompt=line, source=path))

        logger.info("Loaded %d prompts from %s", len(tasks), path)
        return tasks

    def _write_trajectory(self, traj: Trajectory) -> None:
        """
        Append a trajectory to the output JSONL file AND emit an OTel span
        so it appears in Phoenix immediately (no replay needed).
        """
        record = {
            "id": f"batch_{traj.task.idx}",
            "prompt": traj.task.prompt,
            "response": traj.response,
            "thinking": traj.thinking,
            "provider": traj.provider,
            "model": traj.model,
            "tier": traj.tier,
            "domain": traj.task.domain,
            "complexity_score": traj.task.complexity_score,
            "latency_ms": traj.latency_ms,
            "input_tokens": traj.input_tokens,
            "output_tokens": traj.output_tokens,
            "timestamp": traj.timestamp,
            "source": traj.task.source,
        }
        with open(self._output_path, "a") as fh:
            fh.write(json.dumps(record) + "\n")

        # Emit OTel span → Phoenix (silently skipped if Phoenix not running)
        try:
            from able.core.observability.instrumentors import Span, OTelSpanExporter
            import time as _time

            _ts = _time.time()
            _start = _ts - traj.latency_ms / 1000
            _otel_exporter = OTelSpanExporter()
            _otel_exporter.export(Span(
                trace_id=f"batch_{traj.task.idx}",
                span_id=f"b{traj.task.idx:08d}",
                name=f"batch.{traj.task.domain}",
                kind="llm",
                attributes={
                    "model": traj.model,
                    "input_text": traj.task.prompt[:2000],
                    "output_text": traj.response[:2000],
                    "input_tokens": traj.input_tokens,
                    "output_tokens": traj.output_tokens,
                    "tier": traj.tier,
                    "complexity_score": traj.task.complexity_score,
                    "domain": traj.task.domain,
                    "provider": traj.provider,
                    "latency_ms": traj.latency_ms,
                    "source": "batch_runner",
                },
                start_time=_start,
                end_time=_ts,
                status="ok",
            ))
        except Exception:
            pass  # Never block trajectory writing over tracing

    def _load_trajectories_from_disk(self) -> List[Trajectory]:
        """Read trajectories written to the output JSONL file."""
        p = Path(self._output_path)
        if not p.exists():
            return []
        results: List[Trajectory] = []
        with open(p) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                    task = PromptTask(
                        idx=int(d.get("id", "0").split("_")[-1]),
                        prompt=d["prompt"],
                        domain=d.get("domain", ""),
                        complexity_score=d.get("complexity_score"),
                        source=d.get("source", "disk"),
                    )
                    results.append(Trajectory(
                        task=task,
                        response=d.get("response", ""),
                        thinking=d.get("thinking", ""),
                        provider=d.get("provider", ""),
                        model=d.get("model", ""),
                        tier=d.get("tier", 1),
                        complexity_score=d.get("complexity_score", 0.0),
                        latency_ms=d.get("latency_ms", 0),
                        input_tokens=d.get("input_tokens", 0),
                        output_tokens=d.get("output_tokens", 0),
                        timestamp=d.get("timestamp", ""),
                    ))
                except Exception:
                    pass
        return results

    # ── Checkpoint ────────────────────────────────────────────────────────────

    def _save_checkpoint(self) -> None:
        Path(self._checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self._checkpoint_path, "w") as fh:
            json.dump(
                {
                    "completed": sorted(self._completed_idxs),
                    "output_path": self._output_path,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                },
                fh,
            )

    def _load_checkpoint(self) -> None:
        p = Path(self._checkpoint_path)
        if p.exists():
            try:
                with open(p) as fh:
                    data = json.load(fh)
                self._completed_idxs = set(data.get("completed", []))
                logger.info(
                    "Checkpoint loaded: %d tasks already complete",
                    len(self._completed_idxs),
                )
            except Exception as exc:
                logger.warning("Could not load checkpoint: %s", exc)


# ── Minimal OpenAI adapter (for batch mode when OAuth is unavailable) ─────────

class _OpenAIAdapter:
    """Thin wrapper around the openai AsyncClient conforming to provider interface."""

    def __init__(self, api_key: str, model: str = "gpt-4o") -> None:
        self._key = api_key
        self._model = model

    async def complete(self, messages, system: str = "", max_tokens: int = 2048, **_):
        from openai import AsyncOpenAI  # type: ignore[import-untyped]
        from able.core.providers.base import CompletionResult, UsageStats

        client = AsyncOpenAI(api_key=self._key)
        oai_msgs = []
        if system:
            oai_msgs.append({"role": "system", "content": system})
        for m in messages:
            oai_msgs.append({"role": m.role.value, "content": m.content})

        t0 = time.time()
        resp = await client.chat.completions.create(
            model=self._model,
            messages=oai_msgs,
            max_tokens=max_tokens,
        )
        latency_ms = (time.time() - t0) * 1000
        content = resp.choices[0].message.content or ""
        usage = resp.usage
        return CompletionResult(
            content=content,
            finish_reason=resp.choices[0].finish_reason or "stop",
            usage=UsageStats(
                input_tokens=usage.prompt_tokens if usage else 0,
                output_tokens=usage.completion_tokens if usage else 0,
            ),
            provider="openai",
            model=self._model,
            latency_ms=latency_ms,
        )

    @property
    def model(self) -> str:
        return self._model


# ── Built-in coding prompt set (Codex-style tasks) ───────────────────────────

_BUILT_IN_CODING_PROMPTS: List[Dict[str, str]] = [
    # Coding
    {"prompt": "Write a Python function that implements binary search on a sorted list. Include docstring and tests.", "domain": "coding"},
    {"prompt": "Refactor this code to use async/await properly:\n```python\ndef fetch_all(urls):\n    results = []\n    for url in urls:\n        results.append(requests.get(url).json())\n    return results\n```", "domain": "coding"},
    {"prompt": "Implement a rate limiter class in Python using a token bucket algorithm. Thread-safe.", "domain": "coding"},
    {"prompt": "Write a TypeScript function that deep-merges two objects, handling arrays and nested objects correctly.", "domain": "coding"},
    {"prompt": "Debug this Python code and explain what's wrong:\n```python\ndef flatten(lst):\n    result = []\n    for item in lst:\n        if type(item) == list:\n            result.extend(flatten(item))\n        result.append(item)\n    return result\n```", "domain": "coding"},
    {"prompt": "Write a SQL query to find the top 5 customers by total order value in the last 30 days. Tables: orders(id, customer_id, total, created_at), customers(id, name, email).", "domain": "coding"},
    {"prompt": "Implement a simple LRU cache in Python without using any external libraries.", "domain": "coding"},
    {"prompt": "Write a bash script that monitors disk usage and sends an alert if any partition exceeds 80%.", "domain": "coding"},
    {"prompt": "Design a database schema for a multi-tenant SaaS application with row-level security.", "domain": "coding"},
    {"prompt": "Write a React hook that debounces an input value and fetches search results from an API.", "domain": "coding"},
    # Security
    {"prompt": "Explain the OWASP Top 10 vulnerabilities and give a code example for preventing SQL injection in Python.", "domain": "security"},
    {"prompt": "Review this authentication code for security issues:\n```python\ndef login(username, password):\n    query = f\"SELECT * FROM users WHERE username='{username}' AND password='{password}'\"\n    user = db.execute(query).fetchone()\n    return user is not None\n```", "domain": "security"},
    {"prompt": "What is a JWT and how should it be validated securely? Include common pitfalls.", "domain": "security"},
    # DevOps
    {"prompt": "Write a Dockerfile for a Python FastAPI application with a multi-stage build for minimal image size.", "domain": "devops"},
    {"prompt": "Explain how to implement blue-green deployment with nginx and Docker Compose.", "domain": "devops"},
    {"prompt": "Write a GitHub Actions workflow that runs tests, builds a Docker image, and pushes to Docker Hub on merge to main.", "domain": "devops"},
    # Research/Analysis
    {"prompt": "Compare async vs threading vs multiprocessing in Python. When should you use each? Include examples.", "domain": "research"},
    {"prompt": "What are the tradeoffs between PostgreSQL and MongoDB for a high-write, low-read application?", "domain": "research"},
    {"prompt": "Explain consistent hashing and when you'd use it in distributed systems.", "domain": "research"},
    # Planning
    {"prompt": "Design the architecture for a real-time chat application supporting 100k concurrent users. Include technology choices and justification.", "domain": "planning"},
    {"prompt": "Create a migration plan for moving a monolithic Python Django app to microservices without downtime.", "domain": "planning"},
    {"prompt": "What's the right data pipeline architecture for processing 10M events/day with < 1 minute latency?", "domain": "planning"},
    # Writing
    {"prompt": "Write a concise technical blog post explaining why you should never store passwords in plaintext. Target audience: junior developers.", "domain": "copywriting"},
    {"prompt": "Draft a README for an open-source Python library that provides a simple HTTP rate limiter. Include installation, usage examples, and API reference.", "domain": "copywriting"},
]


# ── CLI entry point ──────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="able.core.distillation.batch_runner",
        description="Batch trajectory generator — grows the distillation corpus.",
    )
    p.add_argument("--prompts", help="Path to prompts JSONL/TXT file")
    p.add_argument(
        "--output",
        default=_DEFAULT_OUTPUT,
        help=f"Output JSONL path (default: {_DEFAULT_OUTPUT})",
    )
    p.add_argument(
        "--checkpoint",
        default=_DEFAULT_CHECKPOINT,
        help=f"Checkpoint file path (default: {_DEFAULT_CHECKPOINT})",
    )
    p.add_argument(
        "--concurrent",
        type=int,
        default=3,
        help="Max concurrent provider calls (default: 3)",
    )
    p.add_argument(
        "--codex",
        action="store_true",
        help="Pull tasks from Codex MCP / built-in coding prompts",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Score and tier-assign only — no LLM calls",
    )
    p.add_argument(
        "--config",
        help="Path to routing_config.yaml (auto-detected if not specified)",
    )
    p.add_argument(
        "--export-chatml",
        action="store_true",
        help="After run, also export a .chatml.jsonl file",
    )
    return p


async def _async_main(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
    )

    runner = BatchTrajectoryRunner(
        output_path=args.output,
        checkpoint_path=args.checkpoint,
        config_path=args.config,
        dry_run=args.dry_run,
    )

    if args.codex:
        result = await runner.run_codex_tasks(max_concurrent=args.concurrent)
    elif args.prompts:
        result = await runner.run(args.prompts, max_concurrent=args.concurrent)
    else:
        print("Error: specify --prompts <file> or --codex", file=sys.stderr)
        return 1

    print(result.summary())

    if args.export_chatml and not args.dry_run:
        count = runner.export_chatml()
        print(f"ChatML export: {count} records")

    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    _args = _build_arg_parser().parse_args()
    sys.exit(asyncio.run(_async_main(_args)))
