"""
Comparison Runner — Side-by-side teacher vs student evaluation.

Runs the same prompts through both models, scores outputs, and
generates a comparison report with quality, latency, and cost metrics.

Used by ValidationGate Stage 2 (teacher-student) and Stage 4 (regression).
"""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PromptResult:
    """Result of running a single prompt through one model."""
    prompt: str = ""
    response: str = ""
    quality_score: float = 0.0     # 0.0-1.0 graded by judge model
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    hallucination_detected: bool = False
    error: str = ""


@dataclass
class PromptComparison:
    """Side-by-side comparison for a single prompt."""
    prompt: str = ""
    teacher: Optional[PromptResult] = None
    student: Optional[PromptResult] = None
    quality_delta: float = 0.0     # student - teacher
    latency_delta_ms: float = 0.0  # student - teacher


@dataclass
class ComparisonReport:
    """Aggregate report from a full comparison run."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    teacher_model: str = ""
    student_model: str = ""
    prompt_count: int = 0

    # Quality
    teacher_avg_score: float = 0.0
    student_avg_score: float = 0.0
    quality_ratio: float = 0.0      # student / teacher

    # Hallucination
    teacher_hallucination_rate: float = 0.0
    student_hallucination_rate: float = 0.0

    # Latency
    teacher_avg_latency_ms: float = 0.0
    student_avg_latency_ms: float = 0.0

    # Cost
    teacher_total_cost_usd: float = 0.0
    student_total_cost_usd: float = 0.0

    # Per-prompt details
    comparisons: List[PromptComparison] = field(default_factory=list)

    # Metadata
    duration_ms: float = 0.0
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        """One-line summary for logging."""
        return (
            f"Teacher({self.teacher_model}): {self.teacher_avg_score:.2f} avg | "
            f"Student({self.student_model}): {self.student_avg_score:.2f} avg | "
            f"Ratio: {self.quality_ratio:.1%} | "
            f"Halluc: T={self.teacher_hallucination_rate:.1%} "
            f"S={self.student_hallucination_rate:.1%} | "
            f"Prompts: {self.prompt_count}"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSONL logging."""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "teacher_model": self.teacher_model,
            "student_model": self.student_model,
            "prompt_count": self.prompt_count,
            "teacher_avg_score": self.teacher_avg_score,
            "student_avg_score": self.student_avg_score,
            "quality_ratio": self.quality_ratio,
            "teacher_hallucination_rate": self.teacher_hallucination_rate,
            "student_hallucination_rate": self.student_hallucination_rate,
            "teacher_avg_latency_ms": self.teacher_avg_latency_ms,
            "student_avg_latency_ms": self.student_avg_latency_ms,
            "teacher_total_cost_usd": self.teacher_total_cost_usd,
            "student_total_cost_usd": self.student_total_cost_usd,
            "duration_ms": self.duration_ms,
            "errors": self.errors,
        }


class ComparisonRunner:
    """
    Side-by-side teacher vs student evaluation.

    Runs same prompts through both models, grades outputs with a judge,
    and produces a ComparisonReport with aggregate metrics.

    The runner is model-agnostic — it takes callable providers that
    accept a prompt string and return a response string. This lets it
    work with Ollama (local), OpenRouter, or any other backend.
    """

    def __init__(
        self,
        teacher_provider: Optional[Any] = None,
        student_provider: Optional[Any] = None,
        judge_provider: Optional[Any] = None,
        log_path: str = "data/comparison_results.jsonl",
    ):
        """
        Args:
            teacher_provider: Async callable(prompt: str) -> str for teacher model.
            student_provider: Async callable(prompt: str) -> str for student model.
            judge_provider: Async callable(prompt: str) -> str for grading outputs.
            log_path: Path for JSONL comparison logs.
        """
        self._teacher_provider = teacher_provider
        self._student_provider = student_provider
        self._judge_provider = judge_provider
        self._log_path = log_path

    async def run_comparison(
        self,
        teacher_model: str,
        student_model: str,
        prompts: List[Dict[str, str]],
        concurrency: int = 3,
    ) -> ComparisonReport:
        """
        Run prompts through both models and compare results.

        Args:
            teacher_model: Model identifier for the teacher.
            student_model: Model identifier for the student.
            prompts: List of dicts with at least a "prompt" key.
            concurrency: Max parallel prompt evaluations.

        Returns:
            ComparisonReport with aggregate metrics and per-prompt details.
        """
        start = time.monotonic()
        report = ComparisonReport(
            teacher_model=teacher_model,
            student_model=student_model,
            prompt_count=len(prompts),
        )

        if not prompts:
            report.duration_ms = (time.monotonic() - start) * 1000
            return report

        # Run comparisons with bounded concurrency
        semaphore = asyncio.Semaphore(concurrency)
        tasks = [
            self._compare_single(p, semaphore) for p in prompts
        ]
        comparisons = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results
        teacher_scores: List[float] = []
        student_scores: List[float] = []
        teacher_latencies: List[float] = []
        student_latencies: List[float] = []
        teacher_halluc = 0
        student_halluc = 0
        teacher_cost = 0.0
        student_cost = 0.0

        for comp in comparisons:
            if isinstance(comp, Exception):
                report.errors.append(str(comp))
                continue

            report.comparisons.append(comp)

            if comp.teacher:
                teacher_scores.append(comp.teacher.quality_score)
                teacher_latencies.append(comp.teacher.latency_ms)
                teacher_cost += comp.teacher.cost_usd
                if comp.teacher.hallucination_detected:
                    teacher_halluc += 1

            if comp.student:
                student_scores.append(comp.student.quality_score)
                student_latencies.append(comp.student.latency_ms)
                student_cost += comp.student.cost_usd
                if comp.student.hallucination_detected:
                    student_halluc += 1

        # Aggregate
        report.teacher_avg_score = (
            sum(teacher_scores) / len(teacher_scores) if teacher_scores else 0.0
        )
        report.student_avg_score = (
            sum(student_scores) / len(student_scores) if student_scores else 0.0
        )
        report.quality_ratio = (
            report.student_avg_score / report.teacher_avg_score
            if report.teacher_avg_score > 0 else 0.0
        )

        report.teacher_hallucination_rate = teacher_halluc / len(prompts)
        report.student_hallucination_rate = student_halluc / len(prompts)

        report.teacher_avg_latency_ms = (
            sum(teacher_latencies) / len(teacher_latencies) if teacher_latencies else 0.0
        )
        report.student_avg_latency_ms = (
            sum(student_latencies) / len(student_latencies) if student_latencies else 0.0
        )

        report.teacher_total_cost_usd = teacher_cost
        report.student_total_cost_usd = student_cost

        report.duration_ms = (time.monotonic() - start) * 1000

        logger.info(f"Comparison complete: {report.summary()}")
        self._log_report(report)
        return report

    async def _compare_single(
        self,
        prompt_data: Dict[str, str],
        semaphore: asyncio.Semaphore,
    ) -> PromptComparison:
        """Run a single prompt through both models and grade."""
        async with semaphore:
            prompt_text = prompt_data.get("prompt", "")
            comparison = PromptComparison(prompt=prompt_text)

            # Run both models concurrently
            teacher_task = self._run_model(
                self._teacher_provider, prompt_text
            )
            student_task = self._run_model(
                self._student_provider, prompt_text
            )
            teacher_result, student_result = await asyncio.gather(
                teacher_task, student_task, return_exceptions=True
            )

            if isinstance(teacher_result, Exception):
                comparison.teacher = PromptResult(
                    prompt=prompt_text, error=str(teacher_result)
                )
            else:
                comparison.teacher = teacher_result

            if isinstance(student_result, Exception):
                comparison.student = PromptResult(
                    prompt=prompt_text, error=str(student_result)
                )
            else:
                comparison.student = student_result

            # Grade both outputs with judge
            if comparison.teacher and not comparison.teacher.error:
                comparison.teacher.quality_score = await self._grade_output(
                    prompt_text, comparison.teacher.response
                )
                comparison.teacher.hallucination_detected = (
                    await self._check_hallucination(
                        prompt_text, comparison.teacher.response
                    )
                )

            if comparison.student and not comparison.student.error:
                comparison.student.quality_score = await self._grade_output(
                    prompt_text, comparison.student.response
                )
                comparison.student.hallucination_detected = (
                    await self._check_hallucination(
                        prompt_text, comparison.student.response
                    )
                )

            # Compute deltas
            if comparison.teacher and comparison.student:
                comparison.quality_delta = (
                    comparison.student.quality_score
                    - comparison.teacher.quality_score
                )
                comparison.latency_delta_ms = (
                    comparison.student.latency_ms
                    - comparison.teacher.latency_ms
                )

            return comparison

    async def _run_model(
        self, provider: Optional[Any], prompt: str
    ) -> PromptResult:
        """Run a prompt through a model provider."""
        if not provider:
            return PromptResult(prompt=prompt, error="No provider configured")

        start = time.monotonic()
        try:
            response = await provider(prompt)
            latency = (time.monotonic() - start) * 1000
            return PromptResult(
                prompt=prompt,
                response=response,
                latency_ms=latency,
            )
        except Exception as e:
            return PromptResult(
                prompt=prompt,
                error=str(e),
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def _grade_output(self, prompt: str, response: str) -> float:
        """
        Grade an output using the judge model.

        Returns a score between 0.0 and 1.0.
        Falls back to 0.5 if no judge is configured.
        """
        if not self._judge_provider:
            return 0.5

        judge_prompt = (
            "Rate the following response on a scale of 0.0 to 1.0 for quality, "
            "accuracy, and completeness. Return ONLY a decimal number.\n\n"
            f"PROMPT: {prompt[:500]}\n\n"
            f"RESPONSE: {response[:2000]}\n\n"
            "SCORE:"
        )

        try:
            result = await self._judge_provider(judge_prompt)
            score = float(result.strip().split()[0])
            return max(0.0, min(1.0, score))
        except (ValueError, IndexError):
            return 0.5

    async def _check_hallucination(self, prompt: str, response: str) -> bool:
        """
        Check if a response contains hallucinations.

        Uses pattern matching for common hallucination indicators.
        Falls back to False if no judge is configured.
        """
        # Quick pattern check for obvious hallucination markers
        hallucination_markers = [
            "I don't have access to",
            "As an AI language model",
            "I cannot verify",
            "my training data",
            "I'm not sure if this is accurate",
        ]
        response_lower = response.lower()
        for marker in hallucination_markers:
            if marker.lower() in response_lower:
                return True

        if not self._judge_provider:
            return False

        judge_prompt = (
            "Does this response contain factual errors, made-up information, "
            "or hallucinations? Answer ONLY 'yes' or 'no'.\n\n"
            f"PROMPT: {prompt[:500]}\n\n"
            f"RESPONSE: {response[:2000]}\n\n"
            "HALLUCINATED:"
        )

        try:
            result = await self._judge_provider(judge_prompt)
            return result.strip().lower().startswith("yes")
        except Exception:
            return False

    def _log_report(self, report: ComparisonReport) -> None:
        """Append comparison report to JSONL log."""
        log_path = Path(self._log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(log_path, "a") as f:
                f.write(json.dumps(report.to_dict()) + "\n")
        except OSError as e:
            logger.error(f"Failed to write comparison log: {e}")
