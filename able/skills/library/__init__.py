"""
ABLE Skills Library

Skills are structured as directories with:
- SKILL.md: Behavioral protocol + triggers + I/O schema
- implement.py: Executable code (optional for pure behavioral skills)

Skills Types:
- behavioral: Protocol-driven, SKILL.md is primary (injected into LLM context)
- tool: Action-driven, implement.py is primary (executed as code)
- hybrid: Both protocol and execution are significant

To create a new skill, use SelfImprovementEngine.create_skill()
or manually create a directory following the pattern:

    skills/library/{skill-name}/
        SKILL.md        # Required: triggers, I/O, protocol
        implement.py    # Optional: executable code
"""

from pathlib import Path

LIBRARY_PATH = Path(__file__).parent


def get_available_skills() -> list:
    """List all available skills in the library"""
    skills = []
    for item in LIBRARY_PATH.iterdir():
        if item.is_dir() and (item / "SKILL.md").exists():
            skills.append(item.name)
    return skills


def get_skill_protocol(skill_name: str) -> str:
    """Get the SKILL.md content for a skill"""
    skill_path = LIBRARY_PATH / skill_name / "SKILL.md"
    if skill_path.exists():
        return skill_path.read_text()
    return ""


def should_trigger(skill_name: str, text: str) -> bool:
    """Check if text should trigger a specific skill"""
    skill_dir = LIBRARY_PATH / skill_name
    implement_path = skill_dir / "implement.py"

    if implement_path.exists():
        # Try to load the should_trigger function from implement.py
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                f"{skill_name}_impl",
                implement_path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, "should_trigger"):
                return module.should_trigger(text)
        except Exception:
            pass

    return False
