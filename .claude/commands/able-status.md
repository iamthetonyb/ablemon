---
name: able-status
description: Generate ABLE system status report — objectives, queue, recent activity, provider health
user-invocable: true
---

# /able-status — System Status Report

Generate a comprehensive ABLE status report by reading system state files.

## Steps

1. Read `~/.able/memory/identity.yaml` for operator config
2. Read `~/.able/memory/current_objectives.yaml` for active goals
3. Read today's daily file: `~/.able/memory/daily/$(date +%Y-%m-%d).md`
4. Read `~/.able/queue/pending.yaml` for pending tasks
5. Read recent entries from `~/.able/memory/delegated_tasks.md`
6. Read last 20 lines of `~/.able/memory/learnings.md`

## Output Format

```
═══════════════════════════════════════════════════════════════
ABLE STATUS | [Date] [Time]
═══════════════════════════════════════════════════════════════

CURRENT OBJECTIVES:
[URGENT]: ...
[IN PROGRESS]: ...
[BACKLOG]: n items

PENDING QUEUE: n tasks
DELEGATED: n tasks awaiting results

RECENT LEARNINGS: (last 3)
- ...

READY FOR: [What's next]
═══════════════════════════════════════════════════════════════
```

$ARGUMENTS are ignored — this skill reads system state directly.
