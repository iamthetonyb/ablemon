---
name: atlas-status
description: Generate ATLAS system status report — objectives, queue, recent activity, provider health
user-invocable: true
---

# /atlas-status — System Status Report

Generate a comprehensive ATLAS status report by reading system state files.

## Steps

1. Read `~/.atlas/memory/identity.yaml` for operator config
2. Read `~/.atlas/memory/current_objectives.yaml` for active goals
3. Read today's daily file: `~/.atlas/memory/daily/$(date +%Y-%m-%d).md`
4. Read `~/.atlas/queue/pending.yaml` for pending tasks
5. Read recent entries from `~/.atlas/memory/delegated_tasks.md`
6. Read last 20 lines of `~/.atlas/memory/learnings.md`

## Output Format

```
═══════════════════════════════════════════════════════════════
ATLAS STATUS | [Date] [Time]
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
