"""
Skill Tester — Validate ATLAS skills before registration.

Checks: format, security scan, package validation, registry entry, load test.

Usage:
    python atlas/skills/library/skill-tester/implement.py <skill-name>
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_LIBRARY = Path(__file__).parent.parent
SKILLS_ROOT = SKILLS_LIBRARY.parent
PROJECT_ROOT = SKILLS_ROOT.parent.parent

REQUIRED_SECTIONS = ["purpose", "trigger"]


def check_format(skill_dir: Path) -> dict:
    """Check SKILL.md exists and has required sections."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return {"ok": False, "detail": "SKILL.md not found"}

    content = skill_md.read_text().lower()
    line_count = len(skill_md.read_text().splitlines())

    missing = [s for s in REQUIRED_SECTIONS if s not in content]
    if missing:
        return {"ok": False, "detail": f"Missing sections: {', '.join(missing)}"}

    warnings = []
    if line_count > 500:
        warnings.append(f"SKILL.md is {line_count} lines (recommended <500)")

    return {"ok": True, "detail": f"{line_count} lines", "warnings": warnings}


def check_security(skill_dir: Path) -> dict:
    """Run malware scanner on skill directory."""
    try:
        # Import scanner
        sys.path.insert(0, str(PROJECT_ROOT))
        from atlas.security.malware_scanner import scan_skill
        import asyncio
        result = asyncio.run(scan_skill(str(skill_dir)))
        is_clean = result in ("CLEAN", "clean")
        return {"ok": is_clean, "detail": f"Scan result: {result}"}
    except ImportError:
        return {"ok": True, "detail": "Scanner not available — skipped"}
    except Exception as e:
        return {"ok": False, "detail": f"Scan error: {e}"}


def check_package(skill_dir: Path) -> dict:
    """Run package_skill.py validation."""
    package_script = SKILLS_ROOT / "scripts" / "package_skill.py"
    if not package_script.exists():
        return {"ok": True, "detail": "package_skill.py not found — skipped"}

    try:
        result = subprocess.run(
            [sys.executable, str(package_script), str(skill_dir)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return {"ok": True, "detail": "Package validation passed"}
        else:
            return {"ok": False, "detail": f"Package error: {result.stderr[:200]}"}
    except Exception as e:
        return {"ok": False, "detail": f"Package check failed: {e}"}


def check_registry(skill_name: str) -> dict:
    """Check if skill is registered in SKILL_INDEX.yaml."""
    index_file = SKILLS_ROOT / "SKILL_INDEX.yaml"
    if not index_file.exists():
        return {"ok": False, "detail": "SKILL_INDEX.yaml not found"}

    content = index_file.read_text()
    # Simple check: is the skill name present as a YAML key?
    if f"\n  {skill_name}:" in content or content.startswith(f"  {skill_name}:"):
        return {"ok": True, "detail": "Found in SKILL_INDEX.yaml"}
    else:
        return {"ok": False, "detail": "Not found in SKILL_INDEX.yaml — add entry"}


def check_load(skill_name: str) -> dict:
    """Attempt to load skill via loader."""
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from atlas.skills.loader import SkillLoader
        loader = SkillLoader(str(SKILLS_LIBRARY))
        skill = loader.load(skill_name)
        if skill:
            return {"ok": True, "detail": "Loaded successfully"}
        else:
            return {"ok": False, "detail": "Loader returned None"}
    except ImportError:
        return {"ok": True, "detail": "Loader not available — skipped"}
    except Exception as e:
        return {"ok": False, "detail": f"Load failed: {e}"}


def test_skill(skill_name: str) -> dict:
    """Run all validation checks on a skill."""
    skill_dir = SKILLS_LIBRARY / skill_name

    if not skill_dir.exists():
        return {
            "skill": skill_name,
            "overall": "FAIL",
            "detail": f"Skill directory not found: {skill_dir}",
        }

    results = {
        "skill": skill_name,
        "format": check_format(skill_dir),
        "security": check_security(skill_dir),
        "package": check_package(skill_dir),
        "registry": check_registry(skill_name),
        "load": check_load(skill_name),
    }

    all_ok = all(r["ok"] for r in results.values() if isinstance(r, dict) and "ok" in r)
    results["overall"] = "PASS" if all_ok else "FAIL"

    return results


def print_report(results: dict):
    """Print formatted test report."""
    skill = results.get("skill", "unknown")
    print(f"\nSKILL TEST REPORT: {skill}")
    print("━" * 40)

    for check in ["format", "security", "package", "registry", "load"]:
        r = results.get(check, {})
        if not isinstance(r, dict):
            continue
        status = "PASS" if r.get("ok") else "FAIL"
        detail = r.get("detail", "")
        icon = "✓" if r.get("ok") else "✗"
        print(f"  {icon} {check.title():10s} [{status}] — {detail}")
        for w in r.get("warnings", []):
            print(f"    ⚠ {w}")

    print("━" * 40)
    print(f"  Overall: {results.get('overall', 'UNKNOWN')}")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python implement.py <skill-name>")
        print("Example: python implement.py copywriting")
        sys.exit(1)

    skill_name = sys.argv[1]
    results = test_skill(skill_name)
    print_report(results)
    sys.exit(0 if results["overall"] == "PASS" else 1)
