"""
Skill Loader - Load and create skills from various sources.
"""

import logging
import yaml
from pathlib import Path
from typing import Optional, Dict, Any

from .registry import SkillRegistry, SkillMetadata, Skill

logger = logging.getLogger(__name__)


class SkillLoader:
    """
    Utility for loading and creating skills.

    Supports:
    - Loading from SKILL.md files
    - Creating from templates
    - Importing from v1 format
    """

    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    def load_from_directory(self, skill_dir: Path) -> Optional[Skill]:
        """
        Load a skill from a directory containing SKILL.md and implementation.

        Directory structure:
            skill-name/
                SKILL.md          # Metadata in markdown
                implement.py      # Python implementation
                implement.sh      # OR bash implementation
                test.py           # Optional tests
        """
        skill_dir = Path(skill_dir)

        if not skill_dir.is_dir():
            logger.warning(f"Not a directory: {skill_dir}")
            return None

        # Load metadata from SKILL.md
        skill_md = skill_dir / 'SKILL.md'
        if skill_md.exists():
            metadata = self._parse_skill_md(skill_md)
        else:
            # Fallback: create basic metadata from directory name
            metadata = SkillMetadata(
                name=skill_dir.name,
                description=f"Skill: {skill_dir.name}"
            )

        # Find implementation
        impl_path = None
        impl_type = None

        for name, itype in [
            ('implement.py', 'python'),
            ('main.py', 'python'),
            ('skill.py', 'python'),
            ('implement.sh', 'bash'),
            ('main.sh', 'bash'),
        ]:
            path = skill_dir / name
            if path.exists():
                impl_path = path
                impl_type = itype
                break

        if not impl_path:
            logger.warning(f"No implementation found in: {skill_dir}")
            return None

        skill = Skill(
            metadata=metadata,
            implementation_path=impl_path,
            implementation_type=impl_type,
            source="v2"
        )

        return skill

    def _parse_skill_md(self, path: Path) -> SkillMetadata:
        """Parse SKILL.md format into SkillMetadata"""
        content = path.read_text()
        lines = content.split('\n')

        metadata = {
            'name': path.parent.name,
            'description': '',
            'trigger_phrases': [],
            'inputs': {},
            'outputs': {},
            'dependencies': [],
        }

        current_section = None
        in_table = False
        table_headers = []

        for line in lines:
            line = line.strip()

            # Section headers
            if line.startswith('# Skill:'):
                metadata['name'] = line.replace('# Skill:', '').strip()
            elif line.startswith('## Purpose'):
                current_section = 'purpose'
            elif line.startswith('## Triggers'):
                current_section = 'triggers'
            elif line.startswith('## Inputs'):
                current_section = 'inputs'
                in_table = False
            elif line.startswith('## Outputs'):
                current_section = 'outputs'
                in_table = False
            elif line.startswith('## Dependencies'):
                current_section = 'dependencies'
            elif line.startswith('##'):
                current_section = None
                in_table = False

            # Content parsing
            elif current_section == 'purpose' and line and not line.startswith('|'):
                metadata['description'] += line + ' '

            elif current_section == 'triggers':
                if line.startswith('- Command:'):
                    trigger = line.replace('- Command:', '').strip().strip('"\'')
                    metadata['trigger_phrases'].append(trigger)

            elif current_section == 'inputs':
                if line.startswith('|') and '---' not in line:
                    parts = [p.strip() for p in line.split('|')[1:-1]]
                    if not in_table:
                        # First row is headers
                        table_headers = parts
                        in_table = True
                    elif len(parts) >= 4:
                        # Data row
                        name = parts[0]
                        if name:
                            metadata['inputs'][name] = {
                                'type': parts[1] if len(parts) > 1 else 'string',
                                'required': parts[2].lower() == 'yes' if len(parts) > 2 else False,
                                'description': parts[3] if len(parts) > 3 else '',
                            }

            elif current_section == 'outputs':
                if line.startswith('|') and '---' not in line:
                    parts = [p.strip() for p in line.split('|')[1:-1]]
                    if not in_table:
                        in_table = True
                    elif len(parts) >= 3:
                        name = parts[0]
                        if name:
                            metadata['outputs'][name] = {
                                'type': parts[1] if len(parts) > 1 else 'string',
                                'description': parts[2] if len(parts) > 2 else '',
                            }

            elif current_section == 'dependencies':
                if line.startswith('- '):
                    dep = line[2:].split(':')[0].strip()
                    metadata['dependencies'].append(dep)

        metadata['description'] = metadata['description'].strip()

        return SkillMetadata(
            name=metadata['name'],
            description=metadata['description'],
            trigger_phrases=metadata['trigger_phrases'],
            inputs=metadata['inputs'],
            outputs=metadata['outputs'],
            dependencies=metadata['dependencies'],
        )

    def create_skill(
        self,
        name: str,
        description: str,
        implementation: str,
        implementation_type: str = 'python',
        inputs: Dict[str, Dict] = None,
        outputs: Dict[str, Dict] = None,
        triggers: list = None,
        dependencies: list = None,
        requires_approval: bool = False,
        trust_level: str = "L2_SUGGEST"
    ) -> Path:
        """
        Create a new skill from scratch.

        Args:
            name: Skill name (will be directory name)
            description: What the skill does
            implementation: The implementation code
            implementation_type: 'python' or 'bash'
            inputs: Input schema
            outputs: Output schema
            triggers: Trigger phrases
            dependencies: Required packages/tools
            requires_approval: Whether approval is needed
            trust_level: Minimum trust level required

        Returns:
            Path to the created skill directory
        """
        skill_dir = self.registry.v2_path / name
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Create SKILL.md
        skill_md = self._generate_skill_md(
            name=name,
            description=description,
            inputs=inputs or {},
            outputs=outputs or {},
            triggers=triggers or [],
            dependencies=dependencies or []
        )
        (skill_dir / 'SKILL.md').write_text(skill_md)

        # Create implementation file
        ext = '.py' if implementation_type == 'python' else '.sh'
        impl_file = skill_dir / f'implement{ext}'
        impl_file.write_text(implementation)

        if implementation_type == 'bash':
            impl_file.chmod(0o755)

        # Create metadata
        metadata = SkillMetadata(
            name=name,
            description=description,
            trigger_phrases=triggers or [],
            inputs=inputs or {},
            outputs=outputs or {},
            dependencies=dependencies or [],
            requires_approval=requires_approval,
            trust_level_required=trust_level,
        )

        # Register
        self.registry.register(
            name=name,
            metadata=metadata,
            implementation=impl_file,
            source="v2"
        )

        # Save index
        self.registry.save_index()

        logger.info(f"Created skill: {name} at {skill_dir}")
        return skill_dir

    def _generate_skill_md(
        self,
        name: str,
        description: str,
        inputs: Dict,
        outputs: Dict,
        triggers: list,
        dependencies: list
    ) -> str:
        """Generate SKILL.md content"""
        md = f"""# Skill: {name}

## Purpose
{description}

## Triggers
"""
        for trigger in triggers:
            md += f'- Command: "{trigger}"\n'

        if not triggers:
            md += "- (No automatic triggers)\n"

        md += """
## Inputs
| Name | Type | Required | Description |
|------|------|----------|-------------|
"""
        for iname, spec in inputs.items():
            required = "Yes" if spec.get('required') else "No"
            md += f"| {iname} | {spec.get('type', 'string')} | {required} | {spec.get('description', '')} |\n"

        if not inputs:
            md += "| (none) | | | |\n"

        md += """
## Outputs
| Name | Type | Description |
|------|------|-------------|
"""
        for oname, spec in outputs.items():
            md += f"| {oname} | {spec.get('type', 'string')} | {spec.get('description', '')} |\n"

        if not outputs:
            md += "| result | any | Skill output |\n"

        md += """
## Dependencies
"""
        for dep in dependencies:
            md += f"- {dep}\n"

        if not dependencies:
            md += "- (none)\n"

        md += """
## Usage
```
atlas skill {name} [args]
```

## Examples
```
# Example usage
atlas skill {name}
```
"""
        return md

    def import_v1_skill(self, v1_skill_dir: Path) -> Optional[Skill]:
        """Import a v1 format skill into v2 format"""
        skill = self.load_from_directory(v1_skill_dir)
        if skill:
            skill.source = "v1"
            # Copy to v2 location
            v2_dir = self.registry.v2_path / skill.metadata.name
            if not v2_dir.exists():
                import shutil
                shutil.copytree(v1_skill_dir, v2_dir)
                skill.implementation_path = v2_dir / skill.implementation_path.name
                skill.source = "v2"

            self.registry.register(
                name=skill.metadata.name,
                metadata=skill.metadata,
                implementation=skill.implementation_path,
                source=skill.source
            )

            return skill
        return None

    # =========================================================================
    # HOT-RELOAD AND CONTEXT INJECTION
    # =========================================================================

    def reload_skill(self, name: str) -> Optional[Skill]:
        """
        Hot-reload a skill after modification.

        Use this after updating SKILL.md or implement.py to pick up changes
        without restarting the system.
        """
        # Check v2 path first
        skill_dir = self.registry.v2_path / name
        if not skill_dir.exists():
            # Try v1 path
            skill_dir = self.registry.v1_path / name
            if not skill_dir.exists():
                raise ValueError(f"Skill {name} not found in v1 or v2 paths")

        # Unregister old version
        if name in self.registry.skills:
            del self.registry.skills[name]
            logger.info(f"Unregistered old version of skill: {name}")

        # Re-load from directory
        skill = self.load_from_directory(skill_dir)
        if skill:
            self.registry.register(
                name=skill.metadata.name,
                metadata=skill.metadata,
                implementation=skill.implementation_path,
                source=skill.source
            )
            logger.info(f"Hot-reloaded skill: {name}")
            return skill

        return None

    def list_skills_for_ai(self) -> str:
        """
        Generate a formatted string of available skills for LLM context injection.

        This string can be injected into the system prompt so the AI knows
        what skills are available and how to trigger them.
        """
        lines = [
            "## Available Skills",
            "",
            "The following skills are available and will auto-trigger on matching phrases:",
            "",
        ]

        for name, skill in self.registry.skills.items():
            meta = skill.metadata
            lines.append(f"### {name}")
            lines.append(f"**Description:** {meta.description}")

            if meta.trigger_phrases:
                triggers = ", ".join([f'"{t}"' for t in meta.trigger_phrases[:5]])
                lines.append(f"**Triggers:** {triggers}")

            if meta.inputs:
                inputs_str = ", ".join(meta.inputs.keys())
                lines.append(f"**Inputs:** {inputs_str}")

            lines.append("")

        lines.extend([
            "---",
            "",
            "To use a skill, simply include the trigger phrase in your request.",
            "Skills will auto-activate when matching phrases are detected.",
        ])

        return "\n".join(lines)

    def get_skill_protocol(self, name: str) -> str:
        """
        Get the SKILL.md protocol content for a skill.

        This is what gets injected into the LLM context when a behavioral
        skill is triggered - it contains the instructions that modify
        how the AI thinks/writes.
        """
        skill = self.registry.skills.get(name)
        if not skill:
            return ""

        skill_dir = skill.implementation_path.parent if skill.implementation_path else None
        if skill_dir:
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                return skill_md.read_text()

        return ""
