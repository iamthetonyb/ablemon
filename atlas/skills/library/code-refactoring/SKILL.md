---
name: code-refactoring
description: "Structured code refactoring with behavior validation. Use this skill whenever refactoring, restructuring, decomposing, or improving code quality. Triggers on: refactor, restructure, decompose, clean up code, extract method, remove duplication, apply SOLID, reduce complexity. Also use when code smells are detected or when a file exceeds 500 lines."
---

# Code Refactoring

> Systematic refactoring with behavior preservation, SOLID principles, and validation.

## When to Use

- **Must Use**: Refactoring requests, code smell remediation, file decomposition (>500 lines), architecture changes
- **Recommended**: After feature completion (cleanup pass), before PR reviews
- **Skip**: Simple variable renames, formatting-only changes

## Process

### Step 1: Understand Current Behavior

Before touching anything:

1. **Map the call graph** — what functions call what, what depends on what
2. **Identify the public API** — which functions/classes are used externally
3. **Run existing tests** (if any) — establish baseline
4. **Document current behavior** in a brief pre-refactor snapshot:

```
## Pre-Refactor Snapshot
- File: gateway.py (1,221 lines)
- Public API: ATLASGateway, ATLAS_TOOL_DEFS, ATLAS_SYSTEM_PROMPT
- External callers: start.py, tests/
- Current test status: N/A (no tests)
- Complexity hotspots: _handle_tool_call (250 lines, 15 branches)
```

### Step 2: Plan Refactoring (SOLID Principles)

Apply in order of impact:

| Principle | Check | Action |
|-----------|-------|--------|
| **S**ingle Responsibility | Does this class/function do >1 thing? | Extract into focused modules |
| **O**pen/Closed | Would adding a feature require modifying this core file? | Use registry/plugin patterns |
| **L**iskov Substitution | Can subclasses substitute without breakage? | Fix interface contracts |
| **I**nterface Segregation | Do callers depend on methods they don't use? | Split interfaces |
| **D**ependency Inversion | Does high-level code import low-level details? | Inject dependencies |

### Step 3: Extract Method (Most Common Refactoring)

```
BEFORE: One function doing 5 things
AFTER:  5 focused functions, each <30 lines

Rules:
- Extract when a code block has a comment explaining what it does
- Extract when you see the same 3+ lines repeated
- Name the extracted function after WHAT it does, not HOW
- Pass only what's needed (no god-objects)
```

### Step 4: Remove Duplication (DRY)

1. Search for repeated patterns: `grep -rn "pattern" src/`
2. Extract shared logic into utilities
3. Use composition over inheritance
4. **Exception**: Don't DRY things that happen to look similar but serve different purposes

### Step 5: Validate After Refactoring

```
## Post-Refactor Validation
- [ ] Public API unchanged (same imports work)
- [ ] All existing tests pass
- [ ] New modules import without errors
- [ ] No circular dependencies introduced
- [ ] Line count per file < 500 (target < 300)
- [ ] Each module has a clear single responsibility
```

### Step 6: Document Changes

```
## Refactoring Summary
- Files changed: N
- Lines before: X → Lines after: Y
- Modules extracted: [list]
- Public API changes: [none / breaking changes]
- Test coverage: [before → after]
```

## Constraints

### Required (MUST)
- MUST preserve all existing public APIs
- MUST run smoke tests after each extraction
- MUST keep backward compatibility unless explicitly approved
- MUST document any breaking changes
- MUST commit after each logical extraction (not one massive commit)

### Prohibited (MUST NOT)
- MUST NOT refactor and add features in the same commit
- MUST NOT change behavior during refactoring (separate concerns)
- MUST NOT remove code that "looks unused" without searching for callers
- MUST NOT introduce new dependencies without justification

## Refactoring Checklist

- [ ] Pre-refactor snapshot documented
- [ ] SOLID analysis completed
- [ ] Extract Methods applied (functions < 30 lines)
- [ ] Duplication removed
- [ ] Smoke tests passing
- [ ] Post-refactor validation complete
- [ ] Summary documented with before/after metrics

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Tests fail after refactor | Revert to last working commit, refactor in smaller steps |
| Circular imports | Extract shared types into a `types.py` or `base.py` module |
| Code still complex | Apply facade pattern — simple interface, complex internals |
| Performance regression | Profile before/after, check for N+1 patterns introduced |
