# ATLAS AGI Refactor — GPT-5.4 Codex Handoff

> **Context**: This document is designed to initialize the advanced reasoning model (OpenAI GPT-5.4 Codex or equivalent 1M+ context agents) for the Phase 2/Long-Term structural refactoring of the ATLAS system.

---

## 1. System State & Recent Wins

ATLAS is currently a robust, lightweight orchestrator-based agentic system.
Recent upgrades include:
- **Modular Gateway**: `gateway.py` decomposed entirely using a `ToolRegistry` pattern (reduced from 1200+ to ~500 lines). Search providers upgraded to include Brave, Perplexity, and Gemini natively.
- **Skill Library Sync**: 25+ skills deployed simultaneously to `.claude/commands/` (CLI) and `atlas/skills/library/` (Telegram). Includes advanced Code Refactoring, UI/UX Design Systems, Security (OWASP), and MCP integrations.
- **Deep NLP Profiling**: Copywriting and Ads modules utilize advanced psychological profiling (Tone Hierarchies, Direction of Motivation, Macro-classifications).
- **Evaluation Pipeline**: `promptfoo` configured in `atlas/evals/` to continuously benchmark response quality across Anthropic, OpenRouter, and Ollama.
- **Self-Improvement**: Core infrastructure established via `.learnings/` where the system logs errors, extracts recurring patterns, and writes its own skills using TDD (Writing-Skills Meta protocol).

---

## 2. Immediate Objectives for the Advanced Agent

Your primary directive is to upgrade ATLAS into a **Proactive AGI System** designed for multi-client scalability.

### A. The Backbone: Redis Streams Integration
1. Migrate the worker queues to **Redis Streams**. This is the optimal stack for handling agentic workers.
2. Ensure backward compatibility with existing asynchronous operations while offloading heavy swarm processing to Redis.

### B. Proactive Persistence Layer
1. The `CronScheduler` in `start.py` or the gateway is currently underutilized/dead code. Wake it up.
2. Develop the **InitiativeEngine**: Enable ATLAS to run background tasks, proactive morning briefings, and autonomous self-improvement loops without user prompting.
3. Establish conversational persistence using event-driven architectures.

### C. Scalability & Memory Management
1. **Session Isolation**: Ensure memory and context states are strictly isolated across multiple concurrent users/clients.
2. **Context Compression**: Integrate `zstandard` (zstd) for lightweight, high-speed compression of historical context windows to maximize the 1M+ token limit effectively.

---

## 3. Best Practices & System Constraints

As GPT-5.4 Codex, you have immense context processing power. Use it responsibly:

### Strict Rule: Never "Struggle" Silently
- The ATLAS `EnforcedOrchestrator` is programmed to reject "I can't do this" behavior. 
- If a primary tool or path fails:
  1. Record the failure in `.learnings/ERRORS.md`.
  2. Implement a fallback tool immediately.
  3. Suggest or execute structural refactoring (via SOLID principles) if the architecture blocks you.

### Leveraging the Global Context
- Pre-load `atlas/core/orchestrator.py`, `gateway/tool_registry.py`, and `skills/SKILL_INDEX.yaml` in your first context window.
- Make sweeping, system-wide refactors in single commits, utilizing the safety net of the `promptfoo` evaluation pipelines to verify nothing broke.
- **SOLID Validation**: Any file you expand beyond 500 lines must be aggressively decomposed using the `code-refactoring` protocols. Extracted functions must be strictly under 30 lines.

### Documentation TDD
- When you invent a new pattern or solve a systemic issue, use the **Writing Skills** protocol (TDD applied to process documentation).
- Before moving on, write a `SKILL.md` that captures the exact rationalizations and fixes for the issue, preventing future agents from making the same mistake.

---

## 4. Prompt Initialization String

*(Copy/Paste this to initialize the session)*

```text
/init_advanced_refactor
Role: OpenAI GPT-5.4 Codex Lead Architect.
Context Window: 1M+ tokens.
Directive: Read `docs/handoffs/GPT5.4_Codex_Refactor_Handoff.md`. Ingest the entire `atlas/core/` and `atlas/skills/` directories. Your objective is Phase 2 of the ATLAS Evolution: Implementing Redis Streams for agentic workers, waking up the Proactive Persistence Layer (InitiativeEngine), and applying zstandard context compression. Maintain backwards compatibility with the Telegram bot. Begin by auditing the CronScheduler and proposing the Redis Stream migration plan.
```
