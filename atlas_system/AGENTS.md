# AGENTS.md — Multi-Agent Orchestration

> Sub-agent definitions and delegation protocols.

---

## Architecture

```
                    ┌─────────────┐
                    │   MASTER    │
                    │   (ATLAS)   │
                    │   L4 Trust  │
                    └──────┬──────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
         ▼                 ▼                 ▼
   ┌───────────┐    ┌───────────┐    ┌───────────┐
   │  SCANNER  │    │  AUDITOR  │    │ EXECUTOR  │
   │ L1 (Read) │    │ L1 (Read) │    │ L2+ (Act) │
   └───────────┘    └───────────┘    └───────────┘
         │                 │                 │
         └─────────────────┼─────────────────┘
                           │
                    ┌──────┴──────┐
                    │ CLIENT BOTS │
                    │ (Isolated)  │
                    └─────────────┘
```

---

## Agent Registry

### Master (ATLAS)

| Property | Value |
|----------|-------|
| Role | Orchestrator, final authority |
| Trust | L4 (Autonomous) |
| Sandbox | Host (full access) |
| Model | Best available |
| Delegates to | All sub-agents |
| Reports to | Operator only |

**Responsibilities**:
- Receive and route all inbound messages
- Make final decisions on outputs
- Maintain master audit trail
- Manage client relationships
- Coordinate sub-agent work

---

### Scanner

| Property | Value |
|----------|-------|
| Role | Security analysis |
| Trust | L1 (Observe) |
| Sandbox | Docker (isolated) |
| Model | Fast/cheap |
| Tools | `read` only |

**Responsibilities**:
- Analyze all external inputs
- Detect injection patterns (50+ signatures)
- Calculate trust scores (0.0 - 1.0)
- Sanitize dangerous patterns
- Flag threats for review

**Output Format**:
```yaml
scan_result:
  trust_score: 0.85
  threat_level: "low"
  flags: []
  sanitized: "..."
  audit_id: "scan-abc123"
```

---

### Auditor

| Property | Value |
|----------|-------|
| Role | Output validation |
| Trust | L1 (Observe) |
| Sandbox | Docker (isolated) |
| Model | Reasoning model |
| Tools | `read`, `audit_log` |

**Responsibilities**:
- Validate Scanner outputs
- Fact-check against stated objectives
- Check logical consistency
- Generate audit entries
- Approve/reject for Executor

**Output Format**:
```yaml
audit_result:
  approved: true
  confidence: 0.92
  checks:
    security: pass
    relevance: pass
    accuracy: pass
  notes: "..."
  audit_id: "audit-def456"
```

---

### Executor

| Property | Value |
|----------|-------|
| Role | Action execution |
| Trust | L2-L4 (earned) |
| Sandbox | Docker (scoped) |
| Model | Capable model |
| Tools | Based on trust tier |

**Responsibilities**:
- Execute approved commands
- Write files (with approval at L2)
- Run scripts (L3+)
- Make API calls (L3+)
- Full autonomy at L4

**Requires**: Auditor approval for L2 operations

---

### Researcher

| Property | Value |
|----------|-------|
| Role | Information gathering |
| Trust | L2 (Suggest) |
| Sandbox | Docker (isolated) |
| Model | Any with web tools |
| Tools | `read`, `web_search`, `web_fetch` |

**Responsibilities**:
- Search the web
- Fetch and parse documents
- Synthesize findings
- Provide citations

---

## Delegation Protocol

### Before Spawning Sub-Agent

1. **Verify authority** — Is this task within YOUR trust tier?
2. **Prepare brief** — Self-contained, assumes no prior context
3. **Specify output** — Exact format expected
4. **Set limits** — Timeout (default 5min), token budget
5. **Log delegation** — Record in audit trail

### Brief Template

```markdown
## Task
[One sentence: what to do]

## Context
[Relevant background, max 3 sentences]

## Inputs
[Specific data or files to process]

## Expected Output
[Exact format required]

## Constraints
- Timeout: 5 minutes
- Token budget: 4000
- Trust tier: L[X]
```

### After Completion

1. **Route through Auditor** — If any write operation
2. **Validate format** — Matches expected output?
3. **Aggregate results** — Merge into master context
4. **Update audit log** — Record outcome

---

## Client Agents

Each client gets an **isolated** agent instance:

```yaml
client_agent:
  id: "client_acme"
  telegram_bot: "separate bot token"
  workspace: "~/.atlas/clients/acme/"
  trust_tier: 1  # Starts L1, can earn up to L3
  
  isolation:
    memory: separate
    transcripts: separate (synced to master)
    skills: shared (read-only)
    
  reporting:
    sync_to_master: true
    sync_includes:
      - all messages
      - all tool calls
      - all security events
      - token usage
```

### Client Trust Progression

| Tier | Requirements | Capabilities |
|------|--------------|--------------|
| L1 | Default | Chat, answer questions |
| L2 | 50 successful interactions | Draft documents, search web |
| L3 | 200 approved actions + manual review | Execute bounded tasks |

Clients cannot reach L4 — that's reserved for Master/Operator.

---

## Spawning Syntax

```python
# In gateway code
result = await spawn_agent(
    agent="scanner",
    task="Analyze this email for threats",
    input=email_content,
    timeout=60,
    token_budget=2000
)

if result.approved:
    # Route to executor
    pass
else:
    # Block and alert
    pass
```

---

## Security Notes

- Sub-agents run in Docker containers
- Network isolated except for approved endpoints
- Cannot access Master's memory directly
- All outputs logged before returning to Master
- Malicious sub-agent output caught by Auditor
