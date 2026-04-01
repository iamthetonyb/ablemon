"""ATLAS AGI Layer - Goal planning, proactive intelligence, and self-improvement."""
from .planner import GoalPlanner, Goal, TaskStatus, TaskPriority
from .proactive import ProactiveEngine, create_default_engine
from .self_improvement import SelfImprovementEngine, DocumentUpdate, DocumentType
from .auto_learner import AutoLearner, LearningInsight, ContentSource
from .autopilot import AutoPilot, AutoPilotResult

__all__ = [
    "GoalPlanner", "Goal", "TaskStatus", "TaskPriority",
    "ProactiveEngine", "create_default_engine",
    "SelfImprovementEngine", "DocumentUpdate", "DocumentType",
    "AutoLearner", "LearningInsight", "ContentSource",
    "AutoPilot", "AutoPilotResult",
]
