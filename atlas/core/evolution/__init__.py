"""
ATLAS M2.7 Self-Evolution Daemon

Background-only daemon that uses MiniMax M2.7 to continuously improve
routing accuracy, scorer weights, and domain adjustments.

5-step cycle: Collect → Analyze → Improve → Validate → Deploy

M2.7 is NEVER user-facing. It only runs as the evolution daemon's brain.
"""

from .daemon import EvolutionDaemon, EvolutionConfig, CycleResult
from .collector import MetricsCollector
from .analyzer import EvolutionAnalyzer, AnalysisResult
from .improver import WeightImprover, Improvement
from .validator import ChangeValidator, ValidationResult
from .deployer import ChangeDeployer, DeployResult
from .self_scheduler import SelfScheduler, ScheduledAction, SchedulerCycleReport
from .morning_report import MorningReporter, MorningReportData
from .research_pipeline import ResearchActionPipeline, ClassifiedAction, PipelineResult
from .code_proposer import CodeProposer, Proposal, ProposerCycleResult

__all__ = [
    "EvolutionDaemon", "EvolutionConfig", "CycleResult",
    "MetricsCollector",
    "EvolutionAnalyzer", "AnalysisResult",
    "WeightImprover", "Improvement",
    "ChangeValidator", "ValidationResult",
    "ChangeDeployer", "DeployResult",
    "SelfScheduler", "ScheduledAction", "SchedulerCycleReport",
    "MorningReporter", "MorningReportData",
    "ResearchActionPipeline", "ClassifiedAction", "PipelineResult",
    "CodeProposer", "Proposal", "ProposerCycleResult",
]
