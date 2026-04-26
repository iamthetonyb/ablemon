# Runtime Optimization Roadmap

This doc converts the local deep-research notes into ABLE-specific engineering work. The common principle is: remove unnecessary work first, keep the hot path predictable, and move optional systems off the default path.

## Already Applied

- Telegram webhook mode replaces long polling in production once HTTPS is configured. This removes the duplicate `getUpdates` conflict class instead of trying to dedupe noisy symptoms.
- Cron has durable scheduled-run claims in SQLite, with empty-DB recovery disabled by default. This prevents restart floods while keeping the current lightweight deployment model.
- Docker deploy prunes unused images and build cache older than 24h while preserving volumes.
- CLI startup keeps optional systems out of the default path: Phoenix/OTel, external ASR, billing, channels, Strix, and federation publish/sync only load when configured or invoked.

## Next High-Value ABLE Changes

1. Stable-prefix prompt layout:
   - Keep system rules, tool schemas, routing contracts, and static repo/domain summaries at the start of provider prompts.
   - Put current user input, fresh telemetry, and retrieved evidence at the end.
   - Add metrics for cache-eligible prefix length and provider-reported cache hits where available.

2. Bounded agent/tool budgets:
   - Keep the current execution monitor and repeated-call guard.
   - Add per-tool budget metadata for expensive fetch, browser, and research tools.
   - Report budget pressure in `/status` and the control plane so slow loops are visible.

3. Tiered capture and training artifacts:
   - Treat raw sessions, audio, images, and tool outputs as the audit source of truth.
   - Treat cleaned ChatML, embeddings, prosody tracks, and model-specific training rows as versioned derived artifacts.
   - Keep scrubbers idempotent so old data is re-cleaned when filters improve.

4. Storage and interchange:
   - Keep SQLite for cron claims and local runtime state.
   - Use JSONL for append-only session/corpus capture.
   - Evaluate Arrow IPC only for heavy cross-runtime feature movement; do not add it to the default runtime path until a measured bottleneck exists.
   - Evaluate Zstandard dictionaries for repetitive small sidecars once Python 3.14 is the deployed baseline everywhere.

5. Serving and local inference:
   - Preserve the 5-tier routing model and quant-pinned local models.
   - Add prefix-cache-aware prompt construction before changing model backends.
   - Evaluate vLLM/MLX-style serving only for local/server model throughput bottlenecks, not for the Telegram/CLI control path.

6. Frontend and Studio:
   - Keep Studio out of the gateway hot path.
   - Profile Studio build/runtime size before adding WebGPU/WebCodecs/WASM assets.
   - Use browser-native APIs for media where possible; ship WASM codecs only when the browser lacks the needed capability.

7. Build/runtime profiles:
   - Treat CLI startup, server throughput, and Studio web-size as separate release targets.
   - Do not use one optimization profile for all three; startup, long-lived gateway behavior, and browser payload size have different bottlenecks.
   - Add measured gates before adopting Bun bytecode, Rust PGO/LTO, or WASM post-processing.

8. Multimodal/audio path:
   - Keep raw audio/video/session artifacts as the source of truth.
   - Use dual-rate processing for audio: richer analysis features at a finer timebase, compact LM-facing features only after extraction.
   - Factor speech-derived state into identity, content, prosody, and residual/confidence streams before compressing everything into one vector.
   - Keep the base reasoning model stable; experiment on adapter, pseudo-token, and distillation layers first.

## Measurement Gates

- CLI cold start and `able chat --help` time.
- Gateway import/init time.
- First-response latency and time to first streamed token.
- p50/p95/p99 latency for Telegram and CLI requests.
- Provider prompt-cache eligibility and reported cache hits where available.
- Docker disk usage before/after deploy prune.
- Corpus artifact growth by raw, cleaned, and export tiers.

## Not Recommended Yet

- Redis/Celery for cron dedupe: unnecessary while the production shape is one gateway container plus SQLite-backed durable claims. Add a queue only when jobs need multi-worker throughput or cross-host scheduling.
- PostgreSQL/pg_cron/pgque: useful if ABLE moves cron state to Postgres or runs multiple workers across hosts. It is not the lowest-risk fix for the current SQLite + Docker volume deployment.
- Rewriting Python hot paths in Rust: measure first. Current bottlenecks are startup/import cost, provider latency, prefill/context churn, and optional subsystem boundaries.
