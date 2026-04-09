# ABLE — Session Changelog

> Historical session work archived from CODE_HANDOFF.md.
> This file is reference-only — not loaded into agent context by default.
> For current state, see CODE_HANDOFF.md.

---

## Session 2026-04-09 — Gateway robustness + Hermes patterns

42-45. Phase 0/1 gateway robustness stack. See CODE_HANDOFF.md "Latest Completed Work".

## Session 2026-04-07 #2 — Buddy auto-care, executors, observability

33. **Buddy auto-care system** — Fixed "neglected" status issue:
    - Root cause: `buddy_walk` cron only restored energy (+8), not hunger or thirst
    - Added new NEED_RESTORE actions: `auto_care` (hunger +12), `auto_tick` (thirst +5), `evolution_deploy` (hunger +15), `distillation` (thirst +10), `session_harvest` (hunger +8), `session_ingest` (thirst +5)
    - `buddy_autonomous_tick()` now always waters thirst (+5), auto-feeds hunger when <50 (+12), always walks energy (+8)
    - `award_distillation_xp()` now also waters (+10 distillation) and feeds (+8 session_harvest)
    - `award_evolution_deploy_xp()` now also feeds (+15 evolution_deploy) and waters (+40 evolve)
    - `BuddyNeedsCheck` proactive check now auto-cares instead of just alerting
    - Result: buddy stays alive during autonomous operation without manual intervention

34. **Session auto-routing to buddy** — All session sources now feed buddy:
    - `ClaudeCodeSessionCheck` (every 5 min) now awards `distillation_xp` on harvest
    - Buddy gains XP + need restoration from Claude Code, gstack, gateway, and distillation sessions
    - Every platform interaction auto-routes to buddy without manual push

35. **5 new executor/runtime components**:
    - **MCP SDK Codegen** (`able/tools/mcp/sdk_gen.py`): Generates typed Python callable wrappers from MCP tool JSON schemas. 8 tests.
    - **Bun Shell Backend** (`able/tools/shell/bun_shell.py`): TypeScript + shell execution via Bun. 6 tests.
    - **WebSocket Streaming** (`/ws` endpoint): Gateway streams `stream_message()` output over WebSocket. JSON frame protocol.
    - **RustPython Sandbox** (`able/tools/sandbox/rustpython_sandbox.py`): Optional sandboxed eval backend. 6 tests.
    - **Callable Tool Catalog SDK** (`tool_registry.generate_callable_sdk()`): Every registered tool becomes `tools_sdk.web_search(query="...")`. 5 tests.

36. **Observability wiring**: Graphify → Trilium auto-filing, eval collection cron, knowledge graph pipeline.

37. **Test results**: 755 passing (91 new + 664 existing), 0 regressions.

38. **Trilium auto-init parent notes** — Fixed silent data loss from empty env vars.

39. **Trilium historic upload cron job** — `trilium-historic-upload` (Sunday 3am).

40. **Phoenix tracer retry mechanism** — Fixed spans silently dropped forever.

41. **Test results**: 825 passing, 0 regressions from these changes.

## Session 2026-04-07 #1 — Studio API layer, gateway metrics/SSE/buddy/chat

33-40. Complete Studio API layer (10 new routes), gateway buddy + metrics endpoints, real-time SSE event stream, Studio buddy dashboard, Studio chat wired through ABLE gateway, dashboard enhancements, metrics_queries shared module, test suite (11 tests).

## Previous Sessions (Items 1-32)

1. **All four learning feedback loops closed** (eval→self-improvement, proactive→evolution, memory→evolution, interaction→distillation).
2. **Resource lifecycle tool** with approval gating.
3. **Control-plane hardened**: all endpoints token-gated.
4. **Operator slash commands expanded**: `/resources`, `/eval`, `/evolve`.
5. **Test coverage**: 712+ tests across buddy, CLI, routing, control plane.
6. **Legacy cleanup**: all 87 bare imports migrated, 5 root-level shims removed.
7. **Buddy system**: 5 starter species + hidden unlock, 3 evolution stages, XP/battles/collection/badges/needs/mood/nudges, autonomous progression via cron.
8. **Streaming output** for `able chat` with fallback.
9. **Rich CLI approval rendering**.
10. **Distillation quality improvements**.
11. **Test fixes** (morning reporter, split test daemon).
12. **Deploy hardening** (git operations as `able` user).
13. **Clean terminal experience** (log suppression, Claude Code-style header, graceful no-buddy).
14. **One-command installer** (`install.sh`, global `able` command).
15. **Terminal UX overhaul** (Phoenix skip, thinking spinner, line editing, ANSI color, response timing).
16. **CLI/runtime hardening validated from outside repo root**.
17. **Observability split** (`.[observability]` extras).
18. **Operator report path + Strix sidecar visibility**.
19. **Buddy wired into all system events** (evolution, briefing, research, autopilot, distillation).
20. **`datetime.utcnow()` eliminated** (0 deprecation warnings).
21. **PhasedCoordinatorProtocol merged**.
22. **AuthManager singleton** (880ms → cached).
23. **BuddyNeedsCheck proactive bug fixed**.
24. **Gateway resilience hardening** (circuit breaker, input validation, rate limiting, tool output wrapping).
25. **Lazy imports** for gateway.py (600ms → 300ms).
26. **Multimodal support** (CLI image/audio, Telegram photos/video/audio, pluggable ASR).
27. **Distillation pipeline gap closed** (CLI sessions feed harvest, ExternalToolHarvester).
28. **Distillation scaffolding stripping** (13+ XML tag types, base64, analytics names).
29. **Universal scaffolding + CommandGuard hardening** (all 8 harvesters, 12K LOC analysis).
30. **Federated distillation network** (7 modules, PII scrubbing, TrustGate ingestion).
31. **Unsloth training exporter** (Colab notebooks, standalone scripts, model configs, GPU budget).
32. **P0/P1 gateway audit hardening** (Docker context, OAuth mount, CI smoke).
