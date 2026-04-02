"""
Agent Swarm System

Spawn specialized sub-agents for parallel task execution.
Supports agent specialization, coordination, and result aggregation.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
import logging

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    """Specialized agent roles"""
    RESEARCHER = "researcher"       # Web research, data gathering
    ANALYST = "analyst"             # Data analysis, pattern recognition
    WRITER = "writer"               # Content generation, copywriting
    CODER = "coder"                 # Code generation, debugging
    REVIEWER = "reviewer"           # Quality assurance, fact-checking
    PLANNER = "planner"             # Task decomposition, strategy
    EXECUTOR = "executor"           # Task execution, action taking
    COORDINATOR = "coordinator"     # Swarm orchestration
    CRITIC = "critic"               # Challenge assumptions, find flaws
    SPECIALIST = "specialist"       # Domain-specific expert


class AgentState(str, Enum):
    """Agent lifecycle states"""
    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"


@dataclass
class AgentMessage:
    """Inter-agent communication message"""
    id: str
    sender_id: str
    recipient_id: Optional[str]  # None = broadcast
    content: Any
    message_type: str  # "task", "result", "query", "response", "signal"
    timestamp: datetime = field(default_factory=datetime.utcnow)
    requires_response: bool = False
    correlation_id: Optional[str] = None  # For request-response pairs


@dataclass
class AgentTask:
    """Task assigned to an agent"""
    id: str
    description: str
    context: Dict[str, Any]
    dependencies: List[str] = field(default_factory=list)
    priority: int = 5
    timeout_seconds: float = 300
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AgentResult:
    """Result from an agent task"""
    task_id: str
    agent_id: str
    success: bool
    output: Any
    error: Optional[str] = None
    execution_time: float = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    completed_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class SwarmAgent:
    """Individual agent in the swarm"""
    id: str
    role: AgentRole
    state: AgentState = AgentState.IDLE
    capabilities: Set[str] = field(default_factory=set)
    current_task: Optional[AgentTask] = None
    results: List[AgentResult] = field(default_factory=list)
    message_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    created_at: datetime = field(default_factory=datetime.utcnow)

    # Agent personality/behavior
    system_prompt: str = ""
    temperature: float = 0.7
    max_retries: int = 3

    def __post_init__(self):
        self.message_queue = asyncio.Queue()


class SwarmCoordinator:
    """
    Orchestrates a swarm of specialized agents for parallel task execution.

    Features:
    - Agent spawning and lifecycle management
    - Task distribution and load balancing
    - Inter-agent communication
    - Result aggregation and consensus
    """

    def __init__(
        self,
        llm_provider: Any = None,
        max_agents: int = 10,
        enable_consensus: bool = True,
    ):
        self.llm_provider = llm_provider
        self.max_agents = max_agents
        self.enable_consensus = enable_consensus

        self.agents: Dict[str, SwarmAgent] = {}
        self.message_bus: asyncio.Queue = asyncio.Queue()
        self.task_queue: asyncio.Queue = asyncio.Queue()
        self.results: Dict[str, AgentResult] = {}
        self.task_dependencies: Dict[str, Set[str]] = {}

        self._running = False
        self._coordinator_task: Optional[asyncio.Task] = None

        # Role-specific system prompts
        self.role_prompts = {
            AgentRole.RESEARCHER: """You are a research specialist. Your job is to:
- Find accurate, up-to-date information
- Verify sources and cross-reference data
- Summarize findings concisely
- Flag uncertainties and gaps""",

            AgentRole.ANALYST: """You are a data analyst. Your job is to:
- Identify patterns and trends
- Draw logical conclusions from data
- Quantify findings when possible
- Challenge assumptions with evidence""",

            AgentRole.WRITER: """You are a direct, no-BS writer. Your job is to:
- Write clear, punchy copy that converts
- Mirror the target audience's voice
- Cut the fluff, get to the point
- Use psychological triggers naturally""",

            AgentRole.CODER: """You are a pragmatic developer. Your job is to:
- Write clean, working code
- Avoid over-engineering
- Fix bugs efficiently
- Document only what's necessary""",

            AgentRole.REVIEWER: """You are a critical reviewer. Your job is to:
- Find flaws and weaknesses
- Verify claims and accuracy
- Suggest concrete improvements
- Be honest, not diplomatic""",

            AgentRole.PLANNER: """You are a strategic planner. Your job is to:
- Break complex tasks into steps
- Identify dependencies and risks
- Allocate resources efficiently
- Anticipate problems before they happen""",

            AgentRole.CRITIC: """You are a constructive critic. Your job is to:
- Challenge every assumption
- Find holes in logic
- Ask uncomfortable questions
- Push for better solutions""",
        }

    async def spawn_agent(
        self,
        role: AgentRole,
        capabilities: Optional[Set[str]] = None,
        system_prompt: Optional[str] = None,
    ) -> SwarmAgent:
        """Spawn a new agent with the specified role"""
        if len(self.agents) >= self.max_agents:
            raise RuntimeError(f"Max agents ({self.max_agents}) reached")

        agent_id = f"{role.value}-{uuid.uuid4().hex[:8]}"

        agent = SwarmAgent(
            id=agent_id,
            role=role,
            capabilities=capabilities or self._default_capabilities(role),
            system_prompt=system_prompt or self.role_prompts.get(role, ""),
        )

        self.agents[agent_id] = agent
        logger.info(f"Spawned agent: {agent_id} with role {role.value}")

        return agent

    def _default_capabilities(self, role: AgentRole) -> Set[str]:
        """Get default capabilities for a role"""
        capabilities_map = {
            AgentRole.RESEARCHER: {"web_search", "read_urls", "summarize"},
            AgentRole.ANALYST: {"analyze_data", "find_patterns", "calculate"},
            AgentRole.WRITER: {"generate_text", "copywriting", "edit"},
            AgentRole.CODER: {"generate_code", "debug", "refactor"},
            AgentRole.REVIEWER: {"fact_check", "review", "critique"},
            AgentRole.PLANNER: {"decompose", "schedule", "prioritize"},
            AgentRole.EXECUTOR: {"execute", "shell", "file_ops"},
            AgentRole.CRITIC: {"challenge", "analyze", "question"},
        }
        return capabilities_map.get(role, set())

    async def assign_task(
        self,
        agent_id: str,
        task: AgentTask,
    ) -> None:
        """Assign a task to a specific agent"""
        if agent_id not in self.agents:
            raise ValueError(f"Unknown agent: {agent_id}")

        agent = self.agents[agent_id]

        # Check dependencies
        for dep_id in task.dependencies:
            if dep_id not in self.results:
                await self.task_queue.put((task, agent_id))
                logger.debug(f"Task {task.id} queued - waiting for dependency {dep_id}")
                return

        agent.current_task = task
        agent.state = AgentState.WORKING

        logger.info(f"Assigned task {task.id} to agent {agent_id}")

    async def broadcast(self, message: AgentMessage) -> None:
        """Broadcast a message to all agents"""
        for agent in self.agents.values():
            await agent.message_queue.put(message)

    async def send_message(
        self,
        sender_id: str,
        recipient_id: str,
        content: Any,
        message_type: str = "message",
    ) -> None:
        """Send a message between agents"""
        message = AgentMessage(
            id=uuid.uuid4().hex,
            sender_id=sender_id,
            recipient_id=recipient_id,
            content=content,
            message_type=message_type,
        )

        if recipient_id in self.agents:
            await self.agents[recipient_id].message_queue.put(message)
        else:
            logger.warning(f"Unknown recipient: {recipient_id}")

    async def execute_swarm_task(
        self,
        goal: str,
        roles: List[AgentRole],
        context: Optional[Dict[str, Any]] = None,
        parallel: bool = True,
    ) -> Dict[str, AgentResult]:
        """
        Execute a complex task using a swarm of agents.

        1. Spawns agents with specified roles
        2. Decomposes goal into sub-tasks
        3. Assigns tasks to agents
        4. Coordinates execution (parallel or sequential)
        5. Aggregates and returns results
        """
        context = context or {}
        results = {}

        # Spawn agents
        agents = []
        for role in roles:
            agent = await self.spawn_agent(role)
            agents.append(agent)

        # Decompose goal into tasks (using planner if available)
        tasks = await self._decompose_goal(goal, agents, context)

        if parallel:
            # Execute tasks in parallel
            async def run_agent_task(agent: SwarmAgent, task: AgentTask):
                result = await self._execute_agent_task(agent, task, context)
                results[task.id] = result
                return result

            await asyncio.gather(*[
                run_agent_task(agent, task)
                for agent, task in zip(agents, tasks)
            ])
        else:
            # Execute sequentially with dependency resolution
            for agent, task in zip(agents, tasks):
                result = await self._execute_agent_task(agent, task, context)
                results[task.id] = result
                context[f"result_{task.id}"] = result.output

        # Consensus check if enabled
        if self.enable_consensus and len(results) > 1:
            consensus = await self._build_consensus(results)
            results["_consensus"] = consensus

        # Cleanup
        for agent in agents:
            await self.terminate_agent(agent.id)

        return results

    async def _decompose_goal(
        self,
        goal: str,
        agents: List[SwarmAgent],
        context: Dict[str, Any],
    ) -> List[AgentTask]:
        """Decompose a goal into tasks for each agent"""
        tasks = []

        for i, agent in enumerate(agents):
            task_description = f"[{agent.role.value.upper()}] Part of goal: {goal}"

            # Customize task based on role
            if agent.role == AgentRole.RESEARCHER:
                task_description = f"Research and gather information about: {goal}"
            elif agent.role == AgentRole.ANALYST:
                task_description = f"Analyze data and patterns related to: {goal}"
            elif agent.role == AgentRole.WRITER:
                task_description = f"Write compelling content for: {goal}"
            elif agent.role == AgentRole.REVIEWER:
                task_description = f"Review and verify the work on: {goal}"
            elif agent.role == AgentRole.CRITIC:
                task_description = f"Challenge assumptions and find flaws in: {goal}"

            task = AgentTask(
                id=f"task-{uuid.uuid4().hex[:8]}",
                description=task_description,
                context=context,
                priority=5 - i,  # Earlier agents get higher priority
            )
            tasks.append(task)

        return tasks

    async def _execute_agent_task(
        self,
        agent: SwarmAgent,
        task: AgentTask,
        context: Dict[str, Any],
    ) -> AgentResult:
        """Execute a task with the given agent"""
        start_time = asyncio.get_event_loop().time()

        agent.state = AgentState.WORKING
        agent.current_task = task

        try:
            # Build prompt
            prompt = f"""{agent.system_prompt}

TASK: {task.description}

CONTEXT:
{self._format_context(task.context)}

Execute this task directly. Be specific and actionable."""

            # Call LLM if available
            if self.llm_provider:
                response = await self.llm_provider.generate(
                    prompt=prompt,
                    temperature=agent.temperature,
                )
                output = response.get("content", "")
            else:
                # Mock execution for testing
                output = f"[{agent.role.value}] Processed: {task.description}"

            result = AgentResult(
                task_id=task.id,
                agent_id=agent.id,
                success=True,
                output=output,
                execution_time=asyncio.get_event_loop().time() - start_time,
            )

        except Exception as e:
            logger.error(f"Agent {agent.id} failed: {e}")
            result = AgentResult(
                task_id=task.id,
                agent_id=agent.id,
                success=False,
                output=None,
                error=str(e),
                execution_time=asyncio.get_event_loop().time() - start_time,
            )

        agent.state = AgentState.COMPLETED
        agent.current_task = None
        agent.results.append(result)
        self.results[task.id] = result

        return result

    def _format_context(self, context: Dict[str, Any]) -> str:
        """Format context for prompt"""
        lines = []
        for key, value in context.items():
            if isinstance(value, str) and len(value) > 500:
                value = value[:500] + "..."
            lines.append(f"- {key}: {value}")
        return "\n".join(lines) or "(no context provided)"

    async def _build_consensus(
        self,
        results: Dict[str, AgentResult],
    ) -> AgentResult:
        """Build consensus from multiple agent results"""
        successful = [r for r in results.values() if r.success]

        if not successful:
            return AgentResult(
                task_id="consensus",
                agent_id="coordinator",
                success=False,
                output=None,
                error="No successful results to build consensus from",
            )

        # Aggregate outputs
        combined = "\n\n---\n\n".join([
            f"[{r.agent_id}]:\n{r.output}"
            for r in successful
        ])

        # Use LLM to synthesize if available
        if self.llm_provider:
            prompt = f"""You have received multiple perspectives on a task.
Synthesize them into a unified, actionable response.
Be direct. Cut redundancy. Keep what works.

INPUTS:
{combined}

SYNTHESIS:"""

            response = await self.llm_provider.generate(prompt=prompt)
            consensus_output = response.get("content", combined)
        else:
            consensus_output = combined

        return AgentResult(
            task_id="consensus",
            agent_id="coordinator",
            success=True,
            output=consensus_output,
            metadata={"source_count": len(successful)},
        )

    async def terminate_agent(self, agent_id: str) -> None:
        """Terminate an agent"""
        if agent_id in self.agents:
            agent = self.agents[agent_id]
            agent.state = AgentState.TERMINATED
            del self.agents[agent_id]
            logger.info(f"Terminated agent: {agent_id}")

    async def get_swarm_status(self) -> Dict[str, Any]:
        """Get current swarm status"""
        return {
            "total_agents": len(self.agents),
            "agents": {
                agent_id: {
                    "role": agent.role.value,
                    "state": agent.state.value,
                    "current_task": agent.current_task.id if agent.current_task else None,
                    "completed_tasks": len(agent.results),
                }
                for agent_id, agent in self.agents.items()
            },
            "pending_tasks": self.task_queue.qsize(),
            "completed_results": len(self.results),
        }


class MeshWorkflow:
    """
    Mesh workflow for goal decomposition and parallel execution.

    Usage: /mesh <goal>

    Automatically:
    1. Analyzes the goal
    2. Determines required agent roles
    3. Spawns appropriate swarm
    4. Executes with coordination
    5. Synthesizes results
    """

    def __init__(self, coordinator: SwarmCoordinator):
        self.coordinator = coordinator

    async def execute(
        self,
        goal: str,
        max_depth: int = 3,
        auto_critique: bool = True,
        phased: bool = False,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a mesh workflow for a complex goal.

        Args:
            goal: The high-level task description
            max_depth: Maximum decomposition depth (flat mode only)
            auto_critique: Add critic role automatically (flat mode only)
            phased: If True, use PhasedCoordinatorProtocol (Research -> Synthesis -> Implementation -> Verification)
            context: Optional initial context dict (phased mode only)
        """
        if phased:
            protocol = PhasedCoordinatorProtocol(self.coordinator)
            return await protocol.execute(goal, context)

        # Analyze goal to determine roles needed
        roles = await self._analyze_goal(goal)

        # Add critic if auto-critique enabled
        if auto_critique and AgentRole.CRITIC not in roles:
            roles.append(AgentRole.CRITIC)

        # Execute with swarm
        results = await self.coordinator.execute_swarm_task(
            goal=goal,
            roles=roles,
            parallel=True,
        )

        return {
            "goal": goal,
            "roles_used": [r.value for r in roles],
            "results": {
                task_id: {
                    "agent": result.agent_id,
                    "success": result.success,
                    "output": result.output,
                    "error": result.error,
                }
                for task_id, result in results.items()
            },
            "consensus": results.get("_consensus", {}).output if "_consensus" in results else None,
        }

    async def _analyze_goal(self, goal: str) -> List[AgentRole]:
        """Analyze goal to determine required agent roles"""
        goal_lower = goal.lower()
        roles = []

        # Pattern matching for role selection
        if any(w in goal_lower for w in ["research", "find", "search", "look up"]):
            roles.append(AgentRole.RESEARCHER)

        if any(w in goal_lower for w in ["analyze", "pattern", "data", "trend"]):
            roles.append(AgentRole.ANALYST)

        if any(w in goal_lower for w in ["write", "copy", "content", "email", "respond"]):
            roles.append(AgentRole.WRITER)

        if any(w in goal_lower for w in ["code", "implement", "build", "fix", "debug"]):
            roles.append(AgentRole.CODER)

        if any(w in goal_lower for w in ["review", "check", "verify", "validate"]):
            roles.append(AgentRole.REVIEWER)

        if any(w in goal_lower for w in ["plan", "strategy", "design", "architect"]):
            roles.append(AgentRole.PLANNER)

        # Default to researcher + analyst + writer for general tasks
        if not roles:
            roles = [AgentRole.RESEARCHER, AgentRole.ANALYST, AgentRole.WRITER]

        return roles


class PhasedCoordinatorProtocol:
    """
    4-phase execution protocol for complex agent swarm tasks.
    Inspired by Claude Code's coordinator protocol (Claurst spec).

    Phase 1 (Research): RESEARCHER + ANALYST gather context and information
    Phase 2 (Synthesis): PLANNER + CRITIC synthesize findings into an actionable plan
    Phase 3 (Implementation): CODER + EXECUTOR execute the plan
    Phase 4 (Verification): REVIEWER + CRITIC validate output quality

    Each phase must complete before the next starts.
    Results from each phase feed into the next as context.
    Phase failure halts execution.
    """

    PHASES = [
        ("research", [AgentRole.RESEARCHER, AgentRole.ANALYST]),
        ("synthesis", [AgentRole.PLANNER, AgentRole.CRITIC]),
        ("implementation", [AgentRole.CODER, AgentRole.EXECUTOR]),
        ("verification", [AgentRole.REVIEWER, AgentRole.CRITIC]),
    ]

    def __init__(self, coordinator: SwarmCoordinator):
        self.coordinator = coordinator

    async def execute(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
        phases: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Run goal through phased execution.

        Args:
            goal: The high-level task description
            context: Optional initial context dict
            phases: Optional list of phase names to run (default: all 4).
                    e.g. ["research", "synthesis"] for planning-only tasks.

        Returns:
            Dict with phase results, final output, and metadata.
        """
        context = dict(context or {})
        phase_results: Dict[str, Dict[str, Any]] = {}

        # Filter phases if specified
        active_phases = self.PHASES
        if phases:
            active_phases = [(n, r) for n, r in self.PHASES if n in phases]

        for phase_name, roles in active_phases:
            logger.info(f"Starting phase: {phase_name} with roles: {[r.value for r in roles]}")

            # Inject previous phase results as context
            phase_context = {**context, "previous_phases": phase_results}

            # Build phase-specific goal with previous phase summaries
            phase_goal = f"[{phase_name.upper()} PHASE] {goal}"
            if phase_results:
                prev_summary = "\n\n".join(
                    f"[{name} phase result]: {r.get('output', '')[:500]}"
                    for name, r in phase_results.items()
                )
                phase_goal += f"\n\nPrevious phase results:\n{prev_summary}"

            try:
                results = await self.coordinator.execute_swarm_task(
                    goal=phase_goal,
                    roles=roles,
                    context=phase_context,
                    parallel=True,
                )

                phase_output = self._extract_phase_output(results)
                success = self._check_phase_success(results)

                phase_results[phase_name] = {
                    "output": phase_output,
                    "agents": len(roles),
                    "success": success,
                    "roles": [r.value for r in roles],
                }

                logger.info(f"Phase {phase_name} complete: success={success}")

                if not success:
                    logger.warning(f"Phase {phase_name} failed — halting execution")
                    break

            except Exception as e:
                logger.error(f"Phase {phase_name} error: {e}")
                phase_results[phase_name] = {
                    "output": f"Error: {e}",
                    "agents": len(roles),
                    "success": False,
                    "error": str(e),
                    "roles": [r.value for r in roles],
                }
                break

        return self._build_final_result(goal, active_phases, phase_results)

    def _extract_phase_output(self, results: Dict[str, Any]) -> str:
        """Extract combined output from swarm task results."""
        if not isinstance(results, dict):
            return str(results)

        if "_consensus" in results:
            consensus = results["_consensus"]
            return consensus.output if hasattr(consensus, "output") else str(consensus)

        return "\n\n".join(
            str(getattr(r, "output", r))
            for r in results.values()
            if r and (not hasattr(r, "success") or r.success)
        )

    def _check_phase_success(self, results: Dict[str, Any]) -> bool:
        """Check whether all agents in a phase succeeded."""
        if isinstance(results, dict):
            return all(
                getattr(r, "success", True)
                for r in results.values()
                if hasattr(r, "success")
            )
        return True

    def _build_final_result(
        self,
        goal: str,
        active_phases: List[tuple],
        phase_results: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Assemble the final result dict from all phase results."""
        final_phase = list(phase_results.keys())[-1] if phase_results else None

        # Prefer verification > implementation > last completed phase
        final_output = ""
        if final_phase:
            for candidate in ("verification", "implementation", final_phase):
                if candidate in phase_results:
                    final_output = phase_results[candidate].get("output", "")
                    break

        return {
            "goal": goal,
            "phases_completed": len(phase_results),
            "total_phases": len(active_phases),
            "phases": phase_results,
            "final_output": final_output,
            "success": all(p.get("success", False) for p in phase_results.values()),
        }
