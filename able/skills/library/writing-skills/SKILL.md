---
name: "writing-skills"
description: "Use when creating new skills, editing existing skills, or verifying skills work before deployment. TDD applied to process documentation."
---

# Writing Skills

> **Test-Driven Development (TDD) applied to process documentation.**

## Overview

You write test cases (pressure scenarios with subagents), watch them fail (baseline behavior), write the skill (documentation), watch tests pass (agents comply), and refactor (close loopholes).

**Core principle:** If you didn't watch an agent fail without the skill, you don't know if the skill teaches the right thing.

## TDD Mapping for Skills

| TDD Concept | Skill Creation |
|-------------|----------------|
| **Test case** | Pressure scenario with subagent |
| **Production code** | Skill document (SKILL.md) |
| **Test fails (RED)** | Agent violates rule without skill (baseline) |
| **Test passes (GREEN)** | Agent complies with skill present |
| **Refactor** | Close loopholes while maintaining compliance |

## SKILL.md Structure

**Frontmatter (YAML):**
- Only two fields: `name` and `description` (max 1024 chars total)
- `description`: Third-person, describes ONLY when to use (NOT what it does). Start with "Use when..."

**Body Sections:**
1. **Overview**: Core principle in 1-2 sentences.
2. **When to Use**: Bullet list with symptoms and use cases.
3. **Core Pattern**: Before/after comparison.
4. **Quick Reference**: Table or bullets for scanning.
5. **Implementation**: Code/steps.
6. **Common Mistakes**: What goes wrong + fixes.

## Claude Search Optimization (CSO)

**Critical for discovery:** Future AI agents need to FIND the skill.

1. **Rich Description**: Start with "Use when...". Describe the problem, not the workflow.
   - ❌ BAD: "Summarizes workflow - use for TDD write test first watch fail..."
   - ✅ GOOD: "Use when tests have race conditions or pass inconsistently."
2. **Keyword Coverage**: Include error messages, symptoms, tools.
3. **Token Efficiency**: Frequently-loaded skills < 200 words. Reference `--help` for details instead of listing all flags.
4. **Cross-Referencing**: Don't use `@path/to/SKILL.md` (forces loading). Say: `**REQUIRED SUB-SKILL:** Use [skill-name]`.

## Bulletproofing against Rationalization

Agents are smart and will find loopholes under pressure. Close them:

<Bad>
Write code before test? Delete it.
</Bad>

<Good>
Write code before test? Delete it. Start over.
**No exceptions:**
- Don't keep it as reference
- Don't adapt it while writing tests
</Good>

## RED-GREEN-REFACTOR Cycle

### RED (Failing Test)
1. Run pressure scenario with subagent WITHOUT the skill.
2. Document baseline behavior: What choices did they make? What rationalizations?

### GREEN (Minimal Skill)
1. Write skill addressing those specific rationalizations.
2. Run same scenario WITH skill. Agent should comply.

### REFACTOR (Close Loopholes)
1. If agent finds new rationalization, add explicit counter.
2. Re-test until bulletproof.

```
NO SKILL WITHOUT A FAILING TEST FIRST
```
