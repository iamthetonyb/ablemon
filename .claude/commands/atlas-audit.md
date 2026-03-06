---
name: atlas-audit
description: Run security audit on ATLAS system — scan logs for anomalies, check trust gate events, review injection attempts, generate security report. Triggers on security check, audit, threats.
user-invocable: true
---

# /atlas-audit $ARGUMENTS

Run security audit on ATLAS.

## Audit Steps

1. **Read audit logs**: `~/.atlas/logs/audit/audit.log` and `~/.atlas/audit/logs/*.jsonl`
2. **Check git audit trail**: `python -c "from atlas.audit.git_trail import GitAuditTrail; ..."`
3. **Categorize events**: injection attempts, blocked commands, trust failures, errors
4. **Assess severity**: CRITICAL/HIGH/MEDIUM/LOW/INFO
5. **Generate report**

## Output Format

```markdown
## Security Audit Report

**Period**: $ARGUMENTS (default: last 24h)
**Status**: [clear/warnings/threats_detected]

### Findings
- [severity] [finding title] — [details] — [action taken]

### Statistics
| Event Type | Count |
|------------|-------|
| Injection blocked | n |
| Commands blocked | n |
| Trust failures | n |

### Recommendations
1. ...
```

## Severity Levels
- CRITICAL: Active compromise indicators
- HIGH: Blocked attacks, repeated attempts
- MEDIUM: Single attempts, anomalies
- LOW: Routine blocks
- INFO: Normal activity

Reference: `atlas/skills/library/security-audit/SKILL.md` for full protocol.
