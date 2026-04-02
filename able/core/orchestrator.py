"""
ABLE Skill Orchestrator

Automatically detects user intent and triggers appropriate skills/tools.
No explicit invocation needed - context-aware tool selection.
"""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
import logging

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    """Detected user intent categories"""
    COMMUNICATION = "communication"  # Respond, reply, email, message
    RESEARCH = "research"           # Look up, find out, investigate
    WRITING = "writing"             # Write, draft, create content
    CODING = "coding"               # Code, implement, debug, fix
    ANALYSIS = "analysis"           # Analyze, review, compare
    PLANNING = "planning"           # Plan, strategy, design
    MEMORY = "memory"               # Remember, recall, store
    SYSTEM = "system"               # Status, settings, config
    UNKNOWN = "unknown"


@dataclass
class IntentMatch:
    """A detected intent with confidence"""
    intent: IntentType
    confidence: float  # 0.0 - 1.0
    triggers: List[str]  # Which patterns matched
    extracted: Dict[str, Any] = field(default_factory=dict)  # Extracted parameters


@dataclass
class SkillInvocation:
    """A skill to invoke based on detected intent"""
    skill_name: str
    priority: int  # Higher = invoke first
    parameters: Dict[str, Any]
    depends_on: List[str] = field(default_factory=list)


@dataclass
class ComplexityScore:
    """Complexity scoring for swarm dispatch decision"""
    score: float          # 0.0–1.0
    factors: Dict[str, float]
    use_swarm: bool       # True if score >= SWARM_THRESHOLD
    recommended_roles: List[str] = field(default_factory=list)

    SWARM_THRESHOLD: float = 0.6

    @classmethod
    def calculate(cls, intents: List["IntentMatch"], user_input: str) -> "ComplexityScore":
        """
        Score task complexity to decide direct execution vs swarm dispatch.

        Factors:
        - intent_count: Number of distinct intents detected
        - multi_domain: Spans multiple domains (code + research + writing)
        - length: Long request = more likely complex
        - has_dependencies: Task mentions sequential steps
        - high_stakes: Legal, financial, security topics
        """
        factors = {}

        # Factor 1: Multiple intents
        intent_count = len([i for i in intents if i.confidence >= 0.5])
        factors["intent_count"] = min(1.0, intent_count * 0.25)

        # Factor 2: Multi-domain indicators
        domain_keywords = {
            "code": ["code", "implement", "debug", "build", "function"],
            "research": ["research", "find", "look up", "investigate"],
            "write": ["write", "draft", "email", "blog", "copy"],
            "plan": ["plan", "strategy", "roadmap", "design"],
            "analyze": ["analyze", "review", "compare", "audit"],
        }
        text_lower = user_input.lower()
        domains_hit = sum(
            1 for kws in domain_keywords.values()
            if any(kw in text_lower for kw in kws)
        )
        factors["multi_domain"] = min(1.0, domains_hit * 0.2)

        # Factor 3: Request length
        word_count = len(user_input.split())
        factors["length"] = min(1.0, word_count / 100)

        # Factor 4: Sequential steps mentioned
        step_indicators = ["then", "after that", "next", "finally", "step", "first", "second"]
        has_steps = any(ind in text_lower for ind in step_indicators)
        factors["has_dependencies"] = 0.3 if has_steps else 0.0

        # Factor 5: High-stakes domain
        high_stakes = ["legal", "financial", "security", "production", "deploy", "customer data"]
        is_high_stakes = any(hs in text_lower for hs in high_stakes)
        factors["high_stakes"] = 0.2 if is_high_stakes else 0.0

        score = min(1.0, sum(factors.values()))
        use_swarm = score >= cls.SWARM_THRESHOLD

        # Recommend agent roles based on intents
        role_map = {
            IntentType.RESEARCH: ["RESEARCHER", "ANALYST"],
            IntentType.WRITING: ["WRITER", "REVIEWER"],
            IntentType.CODING: ["CODER", "REVIEWER"],
            IntentType.PLANNING: ["PLANNER", "CRITIC"],
            IntentType.ANALYSIS: ["ANALYST", "REVIEWER"],
            IntentType.COMMUNICATION: ["WRITER"],
        }
        roles = set(["COORDINATOR"])
        for intent in intents:
            if intent.confidence >= 0.5 and intent.intent in role_map:
                roles.update(role_map[intent.intent])

        return cls(
            score=round(score, 2),
            factors=factors,
            use_swarm=use_swarm,
            recommended_roles=list(roles),
        )


@dataclass
class OrchestratorPlan:
    """Execution plan for a user request"""
    intents: List[IntentMatch]
    skills: List[SkillInvocation]
    requires_research: bool = False
    requires_approval: bool = False
    estimated_complexity: str = "simple"  # simple, moderate, complex
    complexity_score: Optional[ComplexityScore] = None


class IntentDetector:
    """
    Detects user intent from natural language input.
    Uses pattern matching + heuristics for fast detection.
    """

    def __init__(self):
        # Intent patterns: (pattern, intent, confidence_boost)
        self.patterns = {
            IntentType.COMMUNICATION: [
                (r'\b(respond|reply|answer)\s+(to|back)', 0.9),
                (r'\bemail\s+\w+', 0.85),
                (r'\b(message|text|reach out)', 0.8),
                (r'\b(draft|compose)\s+.*?(email|message|reply)', 0.9),
                (r'\b(follow up|follow-up)', 0.75),
                (r'\bget back to', 0.8),
            ],
            IntentType.RESEARCH: [
                (r'\b(research|look up|look into|find out)', 0.9),
                (r'\b(investigate|explore|dig into)', 0.85),
                (r'\bwhat (is|are|does|do)\b', 0.6),
                (r'\bhow (to|do|does|can)', 0.6),
                (r'\b(learn about|understand)', 0.75),
                (r'\b(compare|contrast)\b', 0.7),
            ],
            IntentType.WRITING: [
                (r'\bwrite\s+\w+', 0.85),
                (r'\b(create|draft|compose)\s+(content|copy|post)', 0.9),
                (r'\b(blog|article|landing page)', 0.8),
                (r'\b(ad|advertisement|pitch)', 0.85),
                (r'\bcopywriting', 0.95),
                (r'\b(headline|tagline|slogan)', 0.8),
            ],
            IntentType.CODING: [
                (r'\b(code|implement|build|develop)', 0.85),
                (r'\b(fix|debug|solve)\s+.*?(bug|error|issue)', 0.9),
                (r'\b(refactor|optimize|improve)\s+.*?(code|function)', 0.85),
                (r'\bwhy (is|isn\'t|doesn\'t|won\'t)\s+.*?(working|running)', 0.8),
                (r'\b(test|unittest|integration)', 0.75),
            ],
            IntentType.ANALYSIS: [
                (r'\banalyze\b', 0.9),
                (r'\b(review|audit|assess)', 0.85),
                (r'\b(compare|evaluate|benchmark)', 0.8),
                (r'\bwhat\'?s (wrong|the issue)', 0.75),
                (r'\b(breakdown|deep dive)', 0.8),
            ],
            IntentType.PLANNING: [
                (r'\bplan\s+\w+', 0.85),
                (r'\b(strategy|roadmap|approach)', 0.8),
                (r'\bhow (should|can) (we|I)', 0.75),
                (r'\b(design|architect|structure)', 0.8),
                (r'\b(break down|decompose)', 0.75),
            ],
            IntentType.MEMORY: [
                (r'\bremember\s+', 0.9),
                (r'\b(store|save|note)\s+(this|that)', 0.85),
                (r'\brecall\b', 0.9),
                (r'\b(what did|you told me)', 0.8),
                (r'\bforget\b', 0.75),
            ],
            IntentType.SYSTEM: [
                (r'\bstatus\b', 0.9),
                (r'\b(settings|config|configure)', 0.85),
                (r'\bclock (in|out)', 0.9),
                (r'\b(help|commands)', 0.7),
            ],
        }

        # Context keywords that boost certain intents
        self.context_boosters = {
            IntentType.COMMUNICATION: [
                "prospect", "client", "customer", "lead", "contact",
                "inbox", "mailbox", "sender",
            ],
            IntentType.WRITING: [
                "audience", "target", "convert", "persuade", "sell",
                "engagement", "click", "cta",
            ],
            IntentType.RESEARCH: [
                "competitor", "market", "trend", "data", "source",
                "citation", "reference",
            ],
        }

    def detect(self, text: str) -> List[IntentMatch]:
        """Detect intents in user input"""
        text_lower = text.lower()
        matches = []

        for intent, patterns in self.patterns.items():
            triggers = []
            max_confidence = 0.0

            for pattern, confidence in patterns:
                if re.search(pattern, text_lower):
                    triggers.append(pattern)
                    max_confidence = max(max_confidence, confidence)

            # Apply context boosters
            if intent in self.context_boosters:
                for booster in self.context_boosters[intent]:
                    if booster in text_lower:
                        max_confidence = min(1.0, max_confidence + 0.1)

            if triggers:
                matches.append(IntentMatch(
                    intent=intent,
                    confidence=max_confidence,
                    triggers=triggers,
                ))

        # Sort by confidence
        matches.sort(key=lambda m: m.confidence, reverse=True)

        return matches


class ModelRouter:
    """
    Routes tasks to appropriate model tier based on complexity and domain.

    Uses the ComplexityScorer from the routing module when available,
    falls back to simple heuristics otherwise.

    Tier mapping:
        Tier 1 (score < 0.4) → GPT 5.4 Mini xhigh (ChatGPT subscription, $0)
        Tier 2 (score 0.4-0.7) → GPT 5.4 xhigh (ChatGPT subscription, $0)
        Tier 4 (score > 0.7) → Opus 4.6 (premium, budget-gated)
    """

    HIGH_STAKES_DOMAINS = {"legal", "security", "financial", "production", "deploy", "audit"}
    OPUS_INTENTS = {IntentType.PLANNING, IntentType.ANALYSIS}

    # Tier-to-label mapping for backward compat
    TIER_LABELS = {1: "default", 2: "escalation", 4: "premium"}

    def __init__(self):
        self._scorer = None
        self._init_scorer()

    def _init_scorer(self):
        """Try to initialize the complexity scorer from config."""
        try:
            from able.core.routing.complexity_scorer import ComplexityScorer
            from pathlib import Path
            weights_path = Path("config/scorer_weights.yaml")
            if weights_path.exists():
                self._scorer = ComplexityScorer(str(weights_path))
                logger.info("ModelRouter: using ComplexityScorer from config/scorer_weights.yaml")
            else:
                self._scorer = ComplexityScorer()
                logger.info("ModelRouter: using ComplexityScorer with default weights")
        except ImportError:
            logger.info("ModelRouter: ComplexityScorer not available, using legacy routing")

    def select_model(
        self,
        complexity_score: "ComplexityScore",
        intents: List["IntentMatch"],
        user_input: str,
        budget_remaining: float = None,
    ) -> str:
        """
        Route to a model tier. Returns tier label string.

        If ComplexityScorer is available, uses it for multi-tier routing.
        Otherwise falls back to binary premium/default.
        """
        if self._scorer:
            result = self._scorer.score_and_route(
                user_input, budget_remaining=budget_remaining
            )
            tier = result.selected_tier
            label = self.TIER_LABELS.get(tier, "default")
            return label

        # Legacy fallback: binary premium/default
        if complexity_score.score >= 0.7:
            return "premium"

        text_lower = user_input.lower()
        if any(domain in text_lower for domain in self.HIGH_STAKES_DOMAINS):
            return "premium"

        for intent in intents:
            if intent.confidence >= 0.7 and intent.intent in self.OPUS_INTENTS:
                return "premium"

        return "default"

    @property
    def scorer(self):
        """Access the underlying ComplexityScorer (if available)."""
        return self._scorer


class SkillOrchestrator:
    """
    Main orchestrator that plans and executes skill invocations.

    Flow:
    1. Detect intent from user input
    2. Map intents to skills
    3. Route to appropriate model tier (Opus for critical, Sonnet for standard)
    4. Build execution plan
    5. Execute skills in dependency order
    6. Aggregate results
    """

    def __init__(
        self,
        llm_provider: Any = None,
        enable_research: bool = True,
    ):
        self.llm_provider = llm_provider
        self.enable_research = enable_research
        self.intent_detector = IntentDetector()
        self.model_router = ModelRouter()

        # Skill registry: intent -> skill configurations
        self.skill_map = {
            IntentType.COMMUNICATION: [
                SkillInvocation(
                    skill_name="copywriting",
                    priority=10,
                    parameters={"mode": "response"},
                ),
            ],
            IntentType.RESEARCH: [
                SkillInvocation(
                    skill_name="web_search",
                    priority=10,
                    parameters={},
                ),
                SkillInvocation(
                    skill_name="analysis",
                    priority=5,
                    parameters={},
                    depends_on=["web_search"],
                ),
            ],
            IntentType.WRITING: [
                SkillInvocation(
                    skill_name="copywriting",
                    priority=10,
                    parameters={"mode": "create"},
                ),
            ],
            IntentType.CODING: [
                SkillInvocation(
                    skill_name="code_analysis",
                    priority=10,
                    parameters={},
                ),
            ],
            IntentType.ANALYSIS: [
                SkillInvocation(
                    skill_name="analysis",
                    priority=10,
                    parameters={},
                ),
            ],
            IntentType.PLANNING: [
                SkillInvocation(
                    skill_name="goal_planner",
                    priority=10,
                    parameters={},
                ),
            ],
            IntentType.MEMORY: [
                SkillInvocation(
                    skill_name="memory",
                    priority=10,
                    parameters={},
                ),
            ],
        }

        # Skill implementations (lazy loaded)
        self._skills: Dict[str, Callable] = {}

    def register_skill(
        self,
        name: str,
        handler: Callable,
        auto_trigger: Optional[Callable[[str, Dict], bool]] = None,
    ):
        """Register a skill implementation"""
        self._skills[name] = {
            "handler": handler,
            "auto_trigger": auto_trigger,
        }

    async def plan(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> OrchestratorPlan:
        """
        Create an execution plan for the user's request.
        """
        context = context or {}

        # Detect intents
        intents = self.intent_detector.detect(user_input)

        if not intents:
            return OrchestratorPlan(
                intents=[IntentMatch(
                    intent=IntentType.UNKNOWN,
                    confidence=0.0,
                    triggers=[],
                )],
                skills=[],
            )

        # Check for skills that want to auto-trigger
        auto_triggered_skills = []
        for skill_name, skill_info in self._skills.items():
            auto_trigger = skill_info.get("auto_trigger")
            if auto_trigger and auto_trigger(user_input, context):
                auto_triggered_skills.append(SkillInvocation(
                    skill_name=skill_name,
                    priority=15,  # High priority for auto-triggered
                    parameters={"auto_triggered": True},
                ))

        # Map intents to skills
        skills = auto_triggered_skills.copy()
        seen_skills = {s.skill_name for s in skills}

        for intent in intents:
            if intent.confidence < 0.5:
                continue

            skill_configs = self.skill_map.get(intent.intent, [])
            for config in skill_configs:
                if config.skill_name not in seen_skills:
                    skills.append(config)
                    seen_skills.add(config.skill_name)

        # Sort by priority
        skills.sort(key=lambda s: s.priority, reverse=True)

        # Calculate complexity score for swarm decision
        complexity_score = ComplexityScore.calculate(intents, user_input)

        # Determine complexity label
        complexity = "simple"
        if complexity_score.score >= 0.6:
            complexity = "complex"
        elif len(skills) > 2 or complexity_score.score >= 0.35:
            complexity = "moderate"

        # Route to appropriate model tier
        model_tier = self.model_router.select_model(complexity_score, intents, user_input)
        if model_tier == "premium":
            logger.info(
                f"ModelRouter → Opus (premium) for complexity={complexity_score.score:.2f}"
            )

        if complexity_score.use_swarm:
            logger.info(
                f"Complexity score {complexity_score.score:.2f} >= 0.6 → "
                f"Swarm dispatch recommended. Roles: {complexity_score.recommended_roles}"
            )

        # Check if research needed
        requires_research = any(
            i.intent == IntentType.RESEARCH for i in intents
        )

        plan = OrchestratorPlan(
            intents=intents,
            skills=skills,
            requires_research=requires_research,
            estimated_complexity=complexity,
            complexity_score=complexity_score,
        )
        plan.model_tier = model_tier  # 'premium' (Opus) or 'default' (Sonnet)
        return plan

    async def execute(
        self,
        plan: OrchestratorPlan,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute an orchestration plan.
        """
        context = context or {}
        results = {}

        # Group skills by dependency
        no_deps = [s for s in plan.skills if not s.depends_on]
        has_deps = [s for s in plan.skills if s.depends_on]

        # Execute independent skills in parallel
        if no_deps:
            parallel_results = await asyncio.gather(*[
                self._execute_skill(skill, user_input, context)
                for skill in no_deps
            ], return_exceptions=True)

            for skill, result in zip(no_deps, parallel_results):
                if isinstance(result, Exception):
                    results[skill.skill_name] = {
                        "success": False,
                        "error": str(result),
                    }
                else:
                    results[skill.skill_name] = result
                    context[f"result_{skill.skill_name}"] = result

        # Execute dependent skills sequentially
        for skill in has_deps:
            deps_met = all(
                dep in results and results[dep].get("success", False)
                for dep in skill.depends_on
            )

            if deps_met:
                result = await self._execute_skill(skill, user_input, context)
                results[skill.skill_name] = result
                context[f"result_{skill.skill_name}"] = result
            else:
                results[skill.skill_name] = {
                    "success": False,
                    "error": "Dependencies not met",
                    "missing_deps": [
                        dep for dep in skill.depends_on
                        if dep not in results or not results[dep].get("success")
                    ],
                }

        swarm_info = {}
        if plan.complexity_score and plan.complexity_score.use_swarm:
            swarm_info = {
                "swarm_recommended": True,
                "complexity_score": plan.complexity_score.score,
                "recommended_roles": plan.complexity_score.recommended_roles,
                "note": "Use /mesh <goal> to spawn agent swarm for this task",
            }

        return {
            "plan": {
                "intents": [
                    {"type": i.intent.value, "confidence": i.confidence}
                    for i in plan.intents
                ],
                "complexity": plan.estimated_complexity,
                **swarm_info,
            },
            "results": results,
            "success": all(
                r.get("success", False) for r in results.values()
            ),
        }

    async def _execute_skill(
        self,
        skill: SkillInvocation,
        user_input: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute a single skill"""
        skill_info = self._skills.get(skill.skill_name)

        if not skill_info:
            logger.warning(f"Skill not registered: {skill.skill_name}")
            return {
                "success": False,
                "error": f"Skill '{skill.skill_name}' not found",
            }

        handler = skill_info["handler"]

        try:
            start = asyncio.get_event_loop().time()

            if asyncio.iscoroutinefunction(handler):
                result = await handler(
                    user_input,
                    context=context,
                    **skill.parameters,
                )
            else:
                result = handler(
                    user_input,
                    context=context,
                    **skill.parameters,
                )

            return {
                "success": True,
                "output": result,
                "execution_time": asyncio.get_event_loop().time() - start,
            }

        except Exception as e:
            logger.error(f"Skill {skill.skill_name} failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    async def process(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Full processing pipeline: plan → execute → return results.
        """
        plan = await self.plan(user_input, context)

        if not plan.skills:
            return {
                "plan": {"intents": [], "skills": []},
                "results": {},
                "success": True,
                "message": "No skills triggered for this input",
            }

        return await self.execute(plan, user_input, context)


# Convenience function
async def auto_orchestrate(
    user_input: str,
    orchestrator: Optional[SkillOrchestrator] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Automatically orchestrate skill execution for user input.
    """
    if orchestrator is None:
        orchestrator = SkillOrchestrator()

    return await orchestrator.process(user_input, context)


# =============================================================================
# TOOL ATTEMPT TRACKING & NEVER SAY CAN'T ENFORCEMENT
# =============================================================================

@dataclass
class ToolAttemptLog:
    """Tracks all tool attempts for transparency"""
    tool_name: str
    success: bool
    error: Optional[str] = None
    execution_time: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


class ToolAttemptTracker:
    """
    Tracks tool attempts and enforces the "try before saying can't" rule.
    """

    # Patterns that indicate the agent is about to say "can't"
    CANT_PATTERNS = [
        r"i cannot",
        r"i can't",
        r"i don't have access",
        r"i'm not able to",
        r"i am not able to",
        r"i don't have the ability",
        r"i'm unable to",
        r"i am unable to",
        r"outside my capabilities",
        r"beyond my capabilities",
        r"i lack the ability",
        r"not within my capabilities",
        r"i don't have internet",
        r"i cannot browse",
        r"i cannot access external",
    ]

    def __init__(self):
        self.attempts: List[ToolAttemptLog] = []
        self.min_attempts_before_cant = 3

    def log_attempt(
        self,
        tool_name: str,
        success: bool,
        error: str = None,
        execution_time: float = 0.0,
    ):
        """Log a tool attempt"""
        self.attempts.append(ToolAttemptLog(
            tool_name=tool_name,
            success=success,
            error=error,
            execution_time=execution_time,
        ))

    def can_say_cant(self) -> Tuple[bool, str]:
        """
        Check if the agent has made enough attempts to say "can't".

        Returns:
            (allowed, reason) - Whether saying "can't" is allowed
        """
        if len(self.attempts) >= self.min_attempts_before_cant:
            return True, f"Made {len(self.attempts)} attempts"

        remaining = self.min_attempts_before_cant - len(self.attempts)
        return False, f"Must try {remaining} more tools before giving up"

    def would_say_cant(self, response: str) -> bool:
        """Check if a response contains a 'can't' pattern"""
        response_lower = response.lower()
        for pattern in self.CANT_PATTERNS:
            if re.search(pattern, response_lower):
                return True
        return False

    def get_attempt_summary(self) -> str:
        """Get a summary of all attempts made"""
        if not self.attempts:
            return "No tools attempted yet."

        lines = ["Tools attempted:"]
        for attempt in self.attempts:
            status = "✓" if attempt.success else "✗"
            lines.append(f"  {status} {attempt.tool_name} ({attempt.execution_time:.2f}s)")
            if attempt.error:
                lines.append(f"    Error: {attempt.error[:50]}...")
        return "\n".join(lines)

    def clear(self):
        """Clear attempt history for new task"""
        self.attempts = []


class EnforcedOrchestrator(SkillOrchestrator):
    """
    Orchestrator that enforces the NEVER SAY CAN'T protocol.

    Before any response that says "can't", it:
    1. Checks if minimum tool attempts were made
    2. If not, automatically tries relevant tools
    3. Only allows "can't" after trying everything
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tracker = ToolAttemptTracker()

        # Default tools to try when task seems blocked
        self.fallback_tools = [
            "web_search",
            "fetch_url",
            "browser",
            "shell",
            "mcp",
        ]

    async def process_with_enforcement(
        self,
        user_input: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process with NEVER SAY CAN'T enforcement and DUAL-AGENT SWARM logic.
        """
        self.tracker.clear()
        context = context or {}

        # 1. Generate the Plan first to check complexity
        plan = await self.plan(user_input, context)
        is_swarm = plan.complexity_score and plan.complexity_score.score >= 0.7

        # 2. DUAL-AGENT SWARM PATH (Complexity > 0.7)
        if is_swarm:
            logger.info("🐝 Activating Dual-Agent Swarm (Researcher -> Coder)")
            swarm_results = {}
            
            # Agent 1: Researcher (Deep Context Gathering)
            if "web_search" in self._skills:
                research_res = await self._execute_skill(
                    SkillInvocation("web_search", 10, {"query": user_input}),
                    user_input,
                    context
                )
                swarm_results["Researcher"] = research_res
                if research_res.get("success"):
                    context["swarm_research_context"] = str(research_res.get("output"))

            # Agent 2: Coder/Executor (Takes Context and Acts)
            executor_skill = "code_analysis" if "code_analysis" in self._skills else self.fallback_tools[0]
            if executor_skill in self._skills:
                coder_input = f"{user_input}\n\n[RESEARCH CONTEXT]:\n{context.get('swarm_research_context', 'No research found.')}"
                coder_res = await self._execute_skill(
                    SkillInvocation(executor_skill, 10, {}),
                    coder_input,
                    context
                )
                swarm_results["Coder"] = coder_res

            return {
                "plan": {
                    "intents": [{"type": i.intent.value, "confidence": i.confidence} for i in plan.intents],
                    "complexity": plan.estimated_complexity,
                    "swarm_activated": True,
                    "swarm_roles": ["RESEARCHER", "CODER"]
                },
                "results": swarm_results,
                "success": any(r.get("success") for r in swarm_results.values()),
                "tool_attempts": "Swarm Execution Bypassed Normal Tracking",
                "can_say_cant": False,
                "cant_reason": "Swarm active"
            }

        # 3. STANDARD PATH
        result = await self.execute(plan, user_input, context)

        # Check if any skill failed or no skills triggered
        all_failed = not result.get("success", False) or not result.get("results")

        if all_failed:
            # Try fallback tools
            for tool_name in self.fallback_tools:
                if tool_name in self._skills:
                    skill_result = await self._execute_skill(
                        SkillInvocation(
                            skill_name=tool_name,
                            priority=1,
                            parameters={},
                        ),
                        user_input,
                        context,
                    )
                    self.tracker.log_attempt(
                        tool_name=tool_name,
                        success=skill_result.get("success", False),
                        error=skill_result.get("error"),
                        execution_time=skill_result.get("execution_time", 0),
                    )

                    if skill_result.get("success"):
                        return {
                            **result,
                            "success": True,
                            "results": {tool_name: skill_result},
                            "enforcement_triggered": True,
                        }

        # Add attempt summary to result
        result["tool_attempts"] = self.tracker.get_attempt_summary()
        result["can_say_cant"], result["cant_reason"] = self.tracker.can_say_cant()

        return result
