# Runtime Refactor Audit

Date: 2026-04-02
Status: active baseline for runtime-first cleanup

## Boundary Map

### Core
- CLI runtime: `able`, `able chat`, session writing, approvals, slash commands
- Gateway: routing, tools, approvals, memory, rate limiting, prompt enrichment
- Distillation: harvesters, corpus builder, training/export, validation gate
- Evolution: collector, analyzer, deployer, learning loops
- Buddy: onboarding, XP, battles, roster, cron-driven progression
- Control plane: resources, collections, setup, gateway control endpoints
- Studio operator surfaces: settings, resources, collections, setup

### Optional But Kept
- Billing: Stripe and x402 server integrations
- Channels: Slack/Discord adapter library
- ASR backends: external endpoint, Whisper API, local Whisper
- Strix sidecar and self-pentest extras
- Federation live sync/publish path
- Autopilot and research cron extras

### Seed / Template Assets
- Copywriting skill, prompts, evals, and examples
- Distillation prompt-bank seed data
- Sample corpus and onboarding content

### Dead / Accidental
- Empty duplicate source directories ending in ` 2`
- Bare compatibility imports from legacy top-level packages
- Optional systems loading on the default startup path without config

## Decisions

- Preserve the full platform. Do not delete buddy, Studio, federation, control plane, or copywriting/template assets.
- Keep optional systems in-repo, but keep them off the default runtime path unless explicitly configured or invoked.
- Treat billing as webhook/server-only unless payment config is enabled.
- Treat channels as an adapter library, not an active first-class operator surface unless a real runtime entrypoint is wired and validated.
- Treat ASR as opt-in. Do not initialize transcription backends during default gateway startup unless ASR is configured.

## Guardrails

- Hygiene tests block duplicate source directories ending in ` 2`.
- Hygiene tests block bare imports from `core`, `tools`, `memory`, `clients`, `scheduler`, `billing`, and `channels`.
- Runtime-boundary tests verify:
  - `able chat --help` stays off optional subsystems
  - webhook startup skips billing when payment env is disabled
  - webhook billing bootstrap still works when enabled
  - the channels package still imports cleanly as an optional library

## Current Validation Baseline

- Focused boundary/new-surface suites: passing
- Studio production build: passing
- Repo-wide documented suite (`able/tests/`, excluding `test_routing.py` and `test_gateway.py`): 712 passing

Use this document with `CODE_HANDOFF.md` when deciding whether a subsystem should be on the hot path, optional, or treated as a seed asset.
