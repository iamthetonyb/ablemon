# ABLE — Claude Handoff

Date: 2026-04-01
Branch: `codex/able-rewrite-integration`

## What Changed

- Added a real local operator CLI in `able/cli/chat.py` and wired it into `able chat` / `able-chat`.
- Updated `able/__main__.py` so `able` still serves the packaged gateway by default while `able chat` starts a terminal session that reuses the same pipeline, tools, routing, memory, and transcript logging.
- Relaxed the gateway constructor so local CLI mode can run without `TELEGRAM_BOT_TOKEN`; telemetry/session logs now record `cli` as a first-class channel.
- Replaced the gateway's hardcoded tool list with the shared registry in `able/core/gateway/tool_registry.py`.
- Added control-plane endpoints in `able/core/gateway/gateway.py`:
  - `/control/tools/catalog`
  - `/control/resources`
  - `/control/resources/{id}`
  - `/control/resources/{id}/action`
  - `/control/collections`
  - `/control/setup-wizard`
- Added the backend resource plane in `able/core/control_plane/resources.py`.
- Added registry-managed resource tools in `able/core/gateway/tool_defs/resource_tools.py`.
- Expanded tool metadata so studio and runtime share category, approval, risk, read-only, concurrency, surface, and artifact details.
- Removed hardcoded studio defaults and switched `able-studio/app/api/settings/route.ts` + `able-studio/app/settings/page.tsx` to a backend-fed catalog with DB overrides only.
- Added new studio surfaces:
  - `able-studio/app/resources/page.tsx`
  - `able-studio/app/resources/[id]/page.tsx`
  - `able-studio/app/collections/page.tsx`
  - `able-studio/app/setup/page.tsx`
- Added `able-studio/lib/control-plane.ts` plus proxy API routes for tool catalog, resources, collections, and setup.
- Updated deployment to use the packaged `able` entrypoint:
  - `.github/workflows/deploy.yml`
  - `deploy-to-server.sh`
  - `able/able.service`
- Modernized distillation runtime controls:
  - pooled GPU budgets: `t4_colab`, `h100_session`, `local`
  - per-model runtime profiles
  - checkpoint/resume flags
  - T4-first 9B profile, H100-only 27B

## Remote Baseline

Remote heads at the start of this handoff:

- `origin/main` = `3fe6fcf5743299b8a63286650dd79393cda18bf9`
- `origin/feat/session-state-manager` = `830497007f134e493a9191229352aab99fc2a0ef`

The local integration branch was created from `feat/session-state-manager`. The rewrite should supersede the old atlas-era branch/PR stack rather than merging those PRs directly.

## Quant-Pinned Model Roster

- `able-student-27b`
  - `UD-Q4_K_XL` = `17.6 GB`
  - `Q5_K_M` = `19.6 GB`
  - `Q8_0` = `28.6 GB`
- `able-nano-9b`
  - `UD-IQ2_M` = `3.65 GB`
  - `UD-Q4_K_XL` = `5.97 GB`
  - `Q5_K_M` = `6.58 GB`

Source of truth:

- `config/distillation/able_student_27b.yaml`
- `config/distillation/able_nano_9b.yaml`
- `config/ollama/Modelfile.27b`
- `config/ollama/Modelfile.9b-edge`
- `config/ollama/Modelfile.9b-balanced`
- `able/core/distillation/training/quantizer.py`

## What To Verify Next

- Smoke test the new local path with `able chat`, including one read-only tool call and one approval-gated write tool call.
- Decide whether `able chat` should stay terminal-first or grow into a richer TUI. The current path is functional and operator-friendly, but it is still plain terminal I/O rather than a full-screen shell UI.
- Run targeted Python and studio validation. The control plane and new distillation runtime need fresh test coverage.
- Review the resource action path. It currently requires explicit `approved_by` metadata and audit logging, but it is not yet wired into the full Telegram-style approval workflow.
- Decide whether manual `workflow_dispatch ref=...` deploys should reuse the production service or get a separate preview service/path.
- Compare surviving remote PRs `#39` through `#48` against the rewrite and salvage anything still missing before opening the integration PR.
- Push the integration branch and open a superseding PR once the repo-level staging is complete.

## Suggested Checks

```bash
python -m pytest able/tests/test_cli_chat.py
pytest able/tests/test_training_pipeline.py
pytest able/tests/test_distillation_store.py
python -m able chat --help
python -m able.core.distillation.training --check --model 9b --gpu-class t4_colab
cd able-studio && pnpm build
```

## Current Gaps

- `able chat` is now the missing operator entrypoint, but it is still a text REPL. If the target is “better than OpenCode” on UX, the next step is a richer TUI with streaming output, slash-command palettes, artifact panes, and inline approval cards.
- Resource lifecycle actions are operator-gated but not yet approval-workflow-native.
- The studio artifact viewer currently handles JSON/text/HTML artifacts; broader tool-output artifact rendering can extend from there.
- The resource plane is focused on discovery plus controlled actions. Rollback/install orchestration for optional bundles is still thin.
