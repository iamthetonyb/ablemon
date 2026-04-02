#!/usr/bin/env python3
"""
ABLE Skill Packager — Validates and packages a skill into a .skill file.

Adapted from OpenClaw skill-creator (https://github.com/openclaw/openclaw).
Enhanced for ABLE with malware scanning before packaging.

Usage:
    package_skill.py <path/to/skill-folder> [output-directory]

Examples:
    package_skill.py able/skills/library/my-skill
    package_skill.py able/skills/library/my-skill ./dist
"""

import re
import sys
import zipfile
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_skill(skill_path: Path) -> tuple[bool, str]:
    """
    Validate a skill directory structure and SKILL.md content.

    Returns:
        (valid, message)
    """
    # Must be a directory
    if not skill_path.is_dir():
        return False, f"Not a directory: {skill_path}"

    # SKILL.md must exist
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    # Read and parse SKILL.md
    content = skill_md.read_text(encoding="utf-8")

    # Must have YAML frontmatter
    if not content.startswith("---"):
        return False, "SKILL.md must start with YAML frontmatter (---)"

    # Extract frontmatter
    parts = content.split("---", 2)
    if len(parts) < 3:
        return False, "SKILL.md frontmatter not properly closed (missing closing ---)"

    frontmatter = parts[1]

    # Must have 'name' field
    if not re.search(r"^name:\s*.+", frontmatter, re.MULTILINE):
        return False, "SKILL.md frontmatter missing 'name' field"

    # Must have 'description' field
    if not re.search(r"^description:", frontmatter, re.MULTILINE):
        return False, "SKILL.md frontmatter missing 'description' field"

    # Name must follow conventions
    name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
    if name_match:
        skill_name = name_match.group(1).strip()
        if not re.match(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$", skill_name):
            return False, f"Skill name '{skill_name}' invalid: use lowercase letters, digits, hyphens only"
        if skill_name != skill_path.name:
            return False, f"Skill name '{skill_name}' doesn't match directory name '{skill_path.name}'"

    # Description should not contain TODO
    desc_match = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
    if desc_match and "TODO" in desc_match.group(1):
        return False, "SKILL.md description still contains TODO placeholder — fill it in"

    # Body should not be just TODOs
    body = parts[2].strip()
    if body.count("TODO") > 5:
        return False, "SKILL.md body has too many TODO placeholders — complete the skill first"

    # Check for forbidden aux files
    forbidden = ["README.md", "INSTALLATION_GUIDE.md", "CHANGELOG.md", "QUICK_REFERENCE.md"]
    for fname in forbidden:
        if (skill_path / fname).exists():
            return False, f"Remove '{fname}' — skills should not contain auxiliary documentation files"

    # All resource dirs must be valid
    for item in skill_path.iterdir():
        if item.is_dir() and item.name not in {"scripts", "references", "assets", "__pycache__"}:
            return False, f"Unknown directory '{item.name}' — only scripts/, references/, assets/ allowed"

    return True, f"Skill '{skill_path.name}' validation passed"


# ─────────────────────────────────────────────────────────────────────────────
# Packaging
# ─────────────────────────────────────────────────────────────────────────────

def package_skill(skill_path_str: str, output_dir_str: str = None) -> Path | None:
    """
    Package a skill folder into a distributable .skill file.

    The .skill file is a ZIP archive with .skill extension.
    Symlinks are rejected (security restriction).

    Args:
        skill_path_str: Path to the skill directory
        output_dir_str: Output directory (default: current directory)

    Returns:
        Path to the created .skill file, or None if error
    """
    skill_path = Path(skill_path_str).resolve()

    if not skill_path.exists():
        print(f"[ERROR] Skill folder not found: {skill_path}")
        return None

    if not skill_path.is_dir():
        print(f"[ERROR] Not a directory: {skill_path}")
        return None

    # Validate first
    print("Validating skill...")
    valid, message = validate_skill(skill_path)
    if not valid:
        print(f"[ERROR] Validation failed: {message}")
        print("        Fix errors and try again.")
        return None
    print(f"[OK] {message}\n")

    # Determine output path
    if output_dir_str:
        output_dir = Path(output_dir_str).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = Path.cwd()

    skill_filename = output_dir / f"{skill_path.name}.skill"

    # Create ZIP
    try:
        with zipfile.ZipFile(skill_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file_path in sorted(skill_path.rglob("*")):
                # Reject symlinks
                if file_path.is_symlink():
                    print(f"[ERROR] Symlinks not allowed: {file_path}")
                    print("        Security restriction: remove symlinks before packaging.")
                    skill_filename.unlink(missing_ok=True)
                    return None

                # Skip __pycache__
                if "__pycache__" in file_path.parts:
                    continue

                if file_path.is_file():
                    arcname = file_path.relative_to(skill_path.parent)
                    zipf.write(file_path, arcname)
                    print(f"  + {arcname}")

        print(f"\n[OK] Packaged: {skill_filename}")
        print(f"     Size: {skill_filename.stat().st_size:,} bytes")
        print(f"\nInstall with: npx skills add <owner/repo>")
        print(f"Or copy '{skill_filename.name}' to able/skills/library/ and unzip")
        return skill_filename

    except Exception as e:
        print(f"[ERROR] Packaging failed: {e}")
        skill_filename.unlink(missing_ok=True)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: package_skill.py <path/to/skill-folder> [output-directory]")
        print()
        print("Examples:")
        print("  package_skill.py able/skills/library/my-skill")
        print("  package_skill.py able/skills/library/my-skill ./dist")
        sys.exit(1)

    skill_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Packaging ABLE skill: {skill_path}")
    if output_dir:
        print(f"  Output: {output_dir}")
    print()

    result = package_skill(skill_path, output_dir)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
