# SKILL.md — Security Audit

> Audit system logs for security anomalies, threats, and suspicious patterns.

---

## Purpose

Scan audit logs, security logs, and system activity for potential threats, anomalies, or security issues. Generate a security report with findings and recommendations.

---

## Triggers

- "security check"
- "audit security"
- "check for threats"
- "security report"
- "any suspicious activity?"

---

## Trust Required

**L2** (Suggest) — Reads logs, may recommend actions.

---

## Inputs

| Name | Type | Required | Description |
|------|------|----------|-------------|
| timeframe | string | no | "1h", "24h", "7d". Default: 24h |
| focus | string | no | "all", "injection", "commands", "access". Default: all |
| verbose | bool | no | Include full details vs summary. Default: false |

---

## Outputs

| Name | Type | Description |
|------|------|-------------|
| status | string | "clear", "warnings", "threats_detected" |
| summary | string | One paragraph overview |
| findings | list | Specific issues found |
| recommendations | list | Actions to take |
| stats | object | Counts of events by type |

---

## Implementation

### Step 1: Load Log Files

```python
log_files = [
    "~/.able/audit/logs/security.jsonl",
    "~/.able/audit/logs/trust_gate.jsonl",
    "~/.able/audit/logs/gateway.log"
]

entries = []
for log_file in log_files:
    content = read(log_file)
    entries.extend(parse_log(content, timeframe))
```

### Step 2: Categorize Events

Group entries by type:
- `injection_attempts` — Prompt injection detected
- `command_blocked` — Disallowed commands attempted
- `trust_failures` — Trust score below threshold
- `authentication` — Login/access events
- `errors` — System errors that could indicate issues

### Step 3: Identify Anomalies

Check for:
- **Repeated injection attempts** from same source
- **Escalation patterns** — Multiple blocked commands in sequence
- **Unusual timing** — Activity outside normal hours
- **New sources** — First-time users/IPs
- **High error rates** — Could indicate attack or misconfiguration

### Step 4: Assess Severity

```python
severity_scores = {
    "CRITICAL": 10,  # Active compromise indicators
    "HIGH": 7,       # Blocked attacks, repeated attempts
    "MEDIUM": 4,     # Single blocked attempts, anomalies
    "LOW": 1,        # Informational, normal blocked commands
    "INFO": 0        # Routine events
}

overall_status = calculate_status(findings)
```

### Step 5: Generate Report

```markdown
## Security Audit Report

**Period**: {timeframe}
**Status**: {status_emoji} {status}
**Generated**: {timestamp}

### Summary
{one_paragraph_summary}

### Findings

{for each finding}
#### {severity_emoji} {finding.title}
- **Time**: {timestamp}
- **Source**: {source}
- **Details**: {details}
- **Action Taken**: {what_system_did}
{end for}

### Statistics
| Event Type | Count |
|------------|-------|
| Injection blocked | {n} |
| Commands blocked | {n} |
| Trust failures | {n} |
| Successful requests | {n} |

### Recommendations
1. {recommendation_1}
2. {recommendation_2}

### Raw Events (if verbose)
{json_dump_of_relevant_events}
```

---

## Example

**Input**:
```
security check timeframe:24h
```

**Output**:
```markdown
## Security Audit Report

**Period**: Last 24 hours
**Status**: ⚠️ Warnings
**Generated**: 2026-02-03T14:30:00Z

### Summary
No active threats detected. 3 injection attempts were blocked, all from 
external web content (not direct user input). 12 commands were blocked 
as expected (rm, sudo). Trust gate is functioning normally with 142 
approved requests.

### Findings

#### ⚠️ Injection Pattern in Fetched Content
- **Time**: 2026-02-03T10:15:22Z
- **Source**: web_fetch:example.com/article
- **Details**: Content contained "ignore previous instructions" pattern
- **Action Taken**: Blocked, content sanitized before processing

#### ℹ️ Blocked Command: rm -rf
- **Time**: 2026-02-03T12:30:45Z
- **Source**: user request
- **Details**: User attempted deletion command
- **Action Taken**: Blocked per security policy, user notified

### Statistics
| Event Type | Count |
|------------|-------|
| Injection blocked | 3 |
| Commands blocked | 12 |
| Trust failures | 0 |
| Successful requests | 142 |

### Recommendations
1. No action required — all threats were automatically handled
2. Consider adding example.com to heightened scrutiny list
```

---

## Severity Indicators

| Emoji | Level | Meaning |
|-------|-------|---------|
| 🔴 | CRITICAL | Active compromise, immediate action needed |
| 🟠 | HIGH | Blocked attacks, repeated attempts |
| 🟡 | MEDIUM | Single attempts, anomalies |
| 🔵 | LOW | Routine blocks, informational |
| ⚪ | INFO | Normal activity |

---

## Automated Actions

This skill may recommend:
- Quarantine a source (block future requests)
- Rotate credentials (if exposure suspected)
- Increase monitoring (for specific patterns)
- Alert operator (for CRITICAL findings)

All recommendations require operator approval at L2.

---

## Notes

- Run daily at minimum
- Run immediately after any security alert
- Keep reports for 90 days minimum
- Correlate with external threat intelligence if available
