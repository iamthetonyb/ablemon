# SKILL.md — Skill Tester

> Test and validate ATLAS skills before registration.

---

## Purpose

Validate skill format, run security scan, check package integrity, and verify registration. Ensures quality and safety before a skill goes live.

---

## Triggers

- "test skill"
- "validate skill"
- "skill test"
- "check skill"

---

## Trust Required

**L2** (Suggest) — Reads skill files, runs validation scripts.

---

## Inputs

| Name | Type | Required | Description |
|------|------|----------|-------------|
| skill_name | string | yes | Name of skill to test (directory name in library/) |

---

## Outputs

| Name | Type | Description |
|------|------|-------------|
| format_ok | bool | SKILL.md exists and has required sections |
| security_ok | bool | Malware scan returned CLEAN |
| package_ok | bool | Package validation passed |
| registry_ok | bool | Entry exists in SKILL_INDEX.yaml |
| load_ok | bool | Skill loads without errors |
| overall | string | PASS or FAIL |
| details | list | Specific findings |

---

## Implementation

See `implement.py` in this directory.

Steps:
1. Check SKILL.md exists and has Purpose, Triggers sections
2. Run `scan_skill()` from `atlas/security/malware_scanner.py`
3. Run `package_skill.py` validation
4. Check SKILL_INDEX.yaml for entry
5. Attempt to load via `atlas/skills/loader.py`
6. Report results

---

## Notes

- Run this before registering any new skill
- Also run after modifying an existing skill
- Blocks registration if security scan fails
