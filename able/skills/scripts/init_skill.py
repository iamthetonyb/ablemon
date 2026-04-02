#!/usr/bin/env python3
"""
ABLE Skill Initializer — Creates a new skill from template.

Adapted from OpenClaw skill-creator (https://github.com/openclaw/openclaw).
Enhanced for ABLE's multi-agent system with trust levels, malware scanning,
and skills.sh registry integration.

Usage:
    init_skill.py <skill-name> --path <output-directory> [--resources scripts,references,assets] [--examples]

Examples:
    init_skill.py my-skill --path able/skills/library
    init_skill.py my-skill --path able/skills/library --resources scripts,references
    init_skill.py notion-sync --path able/skills/library --resources scripts --examples
"""

import argparse
import re
import sys
from pathlib import Path

MAX_SKILL_NAME_LENGTH = 64
ALLOWED_RESOURCES = {"scripts", "references", "assets"}

SKILL_TEMPLATE = """\
---
name: {skill_name}
description: |
  [TODO: Comprehensive description of what this skill does AND when to use it.
  Include specific trigger phrases, file types, task contexts that activate it.
  This is loaded first — make it decisive. Example:
  "Analyze and optimize Python code for performance and security. Use when:
  (1) User asks to review, audit, or improve code, (2) Debugging errors,
  (3) Refactoring, (4) Any Python file is mentioned with performance issues."]
---

# {skill_title}

## Overview

[TODO: 1-2 sentences on what this skill enables]

## Trust Level

**[L1_OBSERVE | L2_SUGGEST | L3_ACT | L4_AUTONOMOUS]** — [Reason: what it does and why this level]

## Inputs

| Name | Type | Required | Description |
|------|------|----------|-------------|
| [param] | string | yes/no | [What it is] |

## Outputs

| Name | Type | Description |
|------|------|-------------|
| [output] | markdown/dict/string | [What it produces] |

## Workflow

### Step 1: [First Step]

[Concise instructions. Prefer pseudocode or examples over prose.]

```python
# Example
result = do_thing(input)
```

### Step 2: [Second Step]

[Next step...]

## Resources

[Reference any bundled resources here so the agent knows they exist:]

- **scripts/[script.py]** — [What it does, when to run it]
- **references/[ref.md]** — [What it contains, when to read it]
- **assets/[file]** — [What it is, when to use it]

## Error Handling

| Error | Response |
|-------|----------|
| [Error type] | [How to handle] |

## Notes

- [Anything non-obvious the agent needs to know]
"""

EXAMPLE_SCRIPT = """\
#!/usr/bin/env python3
\"\"\"
Script: {skill_name} helper
Part of ABLE skill: {skill_name}

Usage:
    python scripts/{skill_name}.py [args]
\"\"\"

import argparse
import sys


def main(args):
    \"\"\"Main script logic\"\"\"
    print(f"Running {args}")
    # TODO: Implement


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="{skill_name} helper")
    # parser.add_argument("input", help="Input value")
    args = parser.parse_args()
    main(args)
"""

EXAMPLE_REFERENCE = """\
# Reference: {skill_title}

[TODO: Detailed reference documentation loaded only when needed.
Keep SKILL.md lean — put schemas, API specs, examples, and deep docs here.]

## Table of Contents

1. [Section 1](#section-1)
2. [Section 2](#section-2)

## Section 1

[Content...]

## Section 2

[Content...]
"""

EXAMPLE_ASSET = """\
# Asset: {skill_title} Template

[TODO: Replace with actual asset content — templates, boilerplate code,
icons, sample files, etc. Assets are used in output, not loaded into context.]
"""


def normalize_skill_name(skill_name: str) -> str:
    normalized = skill_name.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = normalized.strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized


def title_case_skill_name(skill_name: str) -> str:
    return " ".join(word.capitalize() for word in skill_name.split("-"))


def parse_resources(raw_resources: str) -> list:
    if not raw_resources:
        return []
    resources = [r.strip() for r in raw_resources.split(",") if r.strip()]
    invalid = sorted({r for r in resources if r not in ALLOWED_RESOURCES})
    if invalid:
        print(f"[ERROR] Unknown resource type(s): {', '.join(invalid)}")
        print(f"        Allowed: {', '.join(sorted(ALLOWED_RESOURCES))}")
        sys.exit(1)
    seen, deduped = set(), []
    for r in resources:
        if r not in seen:
            deduped.append(r)
            seen.add(r)
    return deduped


def create_resource_dirs(
    skill_dir: Path,
    skill_name: str,
    skill_title: str,
    resources: list,
    include_examples: bool,
):
    for resource in resources:
        resource_dir = skill_dir / resource
        resource_dir.mkdir(exist_ok=True)

        if resource == "scripts" and include_examples:
            example = resource_dir / f"{skill_name}.py"
            example.write_text(EXAMPLE_SCRIPT.format(skill_name=skill_name))
            example.chmod(0o755)
            print(f"[OK] Created scripts/{skill_name}.py")
        elif resource == "references" and include_examples:
            ref = resource_dir / "reference.md"
            ref.write_text(EXAMPLE_REFERENCE.format(skill_title=skill_title))
            print("[OK] Created references/reference.md")
        elif resource == "assets" and include_examples:
            asset = resource_dir / "template.md"
            asset.write_text(EXAMPLE_ASSET.format(skill_title=skill_title))
            print("[OK] Created assets/template.md")
        else:
            print(f"[OK] Created {resource}/")


def init_skill(skill_name: str, path: str, resources: list, include_examples: bool):
    skill_dir = Path(path).resolve() / skill_name

    if skill_dir.exists():
        print(f"[ERROR] Skill directory already exists: {skill_dir}")
        return None

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
        print(f"[OK] Created skill directory: {skill_dir}")
    except Exception as e:
        print(f"[ERROR] Failed to create directory: {e}")
        return None

    skill_title = title_case_skill_name(skill_name)

    # Write SKILL.md
    skill_md = skill_dir / "SKILL.md"
    try:
        skill_md.write_text(SKILL_TEMPLATE.format(
            skill_name=skill_name,
            skill_title=skill_title,
        ))
        print("[OK] Created SKILL.md")
    except Exception as e:
        print(f"[ERROR] Failed to create SKILL.md: {e}")
        return None

    # Create resource directories
    if resources:
        try:
            create_resource_dirs(skill_dir, skill_name, skill_title, resources, include_examples)
        except Exception as e:
            print(f"[ERROR] Failed to create resource directories: {e}")
            return None

    # Print SKILL_INDEX.yaml snippet
    print(f"\n[OK] Skill '{skill_name}' initialized at {skill_dir}")
    print("\nNext steps:")
    print("  1. Edit SKILL.md — fill in the TODO sections and description")
    if resources:
        print("  2. Implement resources in scripts/, references/, assets/")
    print("  3. Run package_skill.py to validate and package")
    print("  4. Add to SKILL_INDEX.yaml:")
    print(f"""
  {skill_name}:
    description: "[your description]"
    triggers: ["trigger phrase 1", "trigger phrase 2"]
    type: "behavioral|tool|hybrid"
    trust_level: "L1_OBSERVE|L2_SUGGEST|L3_ACT|L4_AUTONOMOUS"
    requires_approval: false
    created: "$(date +%Y-%m-%d)"
    use_count: 0
""")
    print("  5. Run malware scan: python -c \"from able.security.malware_scanner import scan_skill; import asyncio; asyncio.run(scan_skill('able/skills/library/{skill_name}'))\"")

    return skill_dir


def main():
    parser = argparse.ArgumentParser(
        description="Create a new ABLE skill directory with SKILL.md template.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s my-skill --path able/skills/library
  %(prog)s notion-sync --path able/skills/library --resources scripts,references
  %(prog)s pdf-analyzer --path able/skills/library --resources scripts,assets --examples
        """,
    )
    parser.add_argument("skill_name", help="Skill name (normalized to hyphen-case)")
    parser.add_argument("--path", required=True, help="Output directory for the skill")
    parser.add_argument(
        "--resources",
        default="",
        help="Comma-separated list: scripts,references,assets",
    )
    parser.add_argument(
        "--examples",
        action="store_true",
        help="Create example files inside resource directories",
    )
    args = parser.parse_args()

    raw_name = args.skill_name
    skill_name = normalize_skill_name(raw_name)

    if not skill_name:
        print("[ERROR] Skill name must include at least one letter or digit.")
        sys.exit(1)

    if len(skill_name) > MAX_SKILL_NAME_LENGTH:
        print(f"[ERROR] Skill name too long ({len(skill_name)} chars, max {MAX_SKILL_NAME_LENGTH})")
        sys.exit(1)

    if skill_name != raw_name:
        print(f"[NOTE] Normalized: '{raw_name}' → '{skill_name}'")

    resources = parse_resources(args.resources)

    if args.examples and not resources:
        print("[ERROR] --examples requires --resources to be set.")
        sys.exit(1)

    print(f"Initializing ABLE skill: {skill_name}")
    print(f"  Location : {args.path}")
    print(f"  Resources: {', '.join(resources) if resources else 'none'}")
    if args.examples:
        print("  Examples : enabled")
    print()

    result = init_skill(skill_name, args.path, resources, args.examples)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
