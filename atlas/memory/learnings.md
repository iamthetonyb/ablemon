# ATLAS Learnings

> Automatically maintained by the SelfImprovementEngine. New entries appended by daily learnings extraction cron (3am) and self-reflection (Sunday midnight).

---

## 2026-03-19 11:30 | FAILURE_ANALYSIS
**Source**: manual_session_review

## 2026-03-19 11:30 | FAILURE ANALYSIS: Missing cryptography dependency crashed the bot

**What Failed**:
The ATLAS Telegram bot went completely silent — no responses to any messages. The systemd service was crash-looping on startup.

**Root Cause**:
`core/auth/storage.py` imports `from cryptography.fernet import Fernet` but `cryptography` was never added to `requirements.txt`. The import chain `gateway.py → auth/manager.py → auth/storage.py → cryptography` killed the process at module load time, before the Telegram bot could even start polling.

**Prevention**:
1. Made auth imports defensive with try/except in gateway.py — service starts even if cryptography is missing (OAuth is nice-to-have)
2. Added `cryptography>=42.0.0` and `python-dotenv>=1.0.0` to requirements.txt
3. Rule: Every new import from a non-stdlib package MUST be added to requirements.txt in the same commit

---

## 2026-03-19 11:30 | FAILURE ANALYSIS: System prompt was catastrophically incomplete

**What Failed**:
When asked about capabilities, ATLAS responded "I'm a stateless agent, I don't run crons, I don't persist memory" — flatly denying its own running infrastructure.

**Root Cause**:
The `ATLAS_SYSTEM_PROMPT` in `gateway.py` was only 45 lines and only described 8 callable tools. It told the AI nothing about: cron scheduler (5 jobs), evolution daemon, hybrid memory, self-improvement engine, trust gate, complexity routing, agent swarm, goal tracker, fact checker, skill system, or any other AGI capability. The AI had no way to know what it was.

**Prevention**:
1. Rewrote system prompt to describe ALL implemented capabilities
2. Rule: When adding new infrastructure/capabilities to the gateway, ALWAYS update the system prompt to match
3. Rule: The system prompt is the AI's self-knowledge — if it's not in the prompt, the AI doesn't know about it

---

## 2026-03-19 11:30 | FAILURE ANALYSIS: Telegram Markdown parse errors

**What Failed**:
AI responses with unbalanced backticks, asterisks, or underscores caused Telegram to reject the entire message with "can't find end of the entity starting at byte offset X". The error handler showed the raw error to the user instead of the response.

**Root Cause**:
`_handle_master_message` used `parse_mode="Markdown"` with no fallback. LLM output frequently contains imperfect Markdown that Telegram's strict parser rejects.

**Prevention**:
1. Added try/except fallback: try Markdown first, resend as plain text on parse failure
2. Added truncation for tool notifications (Telegram 4096 char limit)
3. Rule: NEVER use `parse_mode="Markdown"` without a plain-text fallback

---

## 2026-03-19 11:30 | FAILURE ANALYSIS: Thinking indicator spam

**What Failed**:
Every tool loop iteration sent "ATLAS is thinking... (Turn X/15)" as a separate Telegram message, creating noise floods of 5-15 messages per response.

**Root Cause**:
The thinking indicator was inside the tool loop, not outside it. It was designed to show the AI was working, but executed on every iteration instead of just once.

**Prevention**:
1. Removed thinking indicator entirely — the response itself is enough
2. Rule: Intermediate status messages should be rare and meaningful, not per-iteration

---

## 2026-03-19 11:30 | WIN
**Source**: manual_session_review

GitHub Actions deploy workflow was using deprecated `actions/checkout@v4` (Node.js 20) and outdated `appleboy/ssh-action@v1.0.3`. Updated to `@v5` and `@v1.2.2` respectively. Node.js 20 deprecation warning eliminated.

---

## 2026-03-19 11:30 | CONVERSATION_ANALYSIS
**Source**: manual_session_review

**Pattern observed**: The operator expects ATLAS to be fully self-aware of its own infrastructure. When the AI denies having capabilities that are demonstrably running (crons, memory, self-improvement), it erodes trust. The system prompt is the AI's self-model — it must be kept in sync with actual deployed infrastructure.

**Pattern observed**: The operator prefers fixes to be pushed to GitHub immediately and deployed automatically via the CI/CD pipeline. Don't just fix locally — always push so the server updates.

---
