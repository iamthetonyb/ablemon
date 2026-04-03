"""
Skill Registry - Central catalog of available skills.
"""

import logging
import yaml
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
import json

logger = logging.getLogger(__name__)


@dataclass
class SkillMetadata:
    """Metadata about a skill"""
    name: str
    description: str
    version: str = "1.0.0"
    author: str = "ABLE"
    created: datetime = field(default_factory=datetime.utcnow)
    last_used: Optional[datetime] = None
    use_count: int = 0

    # Trigger configuration
    trigger_phrases: List[str] = field(default_factory=list)
    file_patterns: List[str] = field(default_factory=list)
    cron_schedule: Optional[str] = None

    # Input/output schema
    inputs: Dict[str, Dict] = field(default_factory=dict)  # name -> {type, required, description}
    outputs: Dict[str, Dict] = field(default_factory=dict)

    # Dependencies
    dependencies: List[str] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)

    # Security
    trust_level_required: str = "L2_SUGGEST"  # Minimum trust level to execute
    requires_approval: bool = False

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "created": self.created.isoformat(),
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "use_count": self.use_count,
            "trigger_phrases": self.trigger_phrases,
            "file_patterns": self.file_patterns,
            "cron_schedule": self.cron_schedule,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "dependencies": self.dependencies,
            "required_tools": self.required_tools,
            "trust_level_required": self.trust_level_required,
            "requires_approval": self.requires_approval,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'SkillMetadata':
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            author=data.get("author", "ABLE"),
            created=datetime.fromisoformat(data["created"]) if "created" in data else datetime.utcnow(),
            last_used=datetime.fromisoformat(data["last_used"]) if data.get("last_used") else None,
            use_count=data.get("use_count", 0),
            trigger_phrases=data.get("trigger_phrases", []),
            file_patterns=data.get("file_patterns", []),
            cron_schedule=data.get("cron_schedule"),
            inputs=data.get("inputs", {}),
            outputs=data.get("outputs", {}),
            dependencies=data.get("dependencies", []),
            required_tools=data.get("required_tools", []),
            trust_level_required=data.get("trust_level_required", "L2_SUGGEST"),
            requires_approval=data.get("requires_approval", False),
        )


@dataclass
class Skill:
    """A registered skill"""
    metadata: SkillMetadata
    implementation_path: Path
    implementation_type: str = "python"  # python, bash, or callable
    callable: Optional[Callable] = None
    source: str = "v2"  # v1 or v2


class SkillRegistry:
    """
    Central registry for all available skills.

    Supports:
    - V1 skills from ~/.able/skills/
    - V2 skills from able/skills/
    - Dynamically registered callables
    """

    def __init__(self, v2_skills_path: Path, v1_skills_path: Optional[Path] = None):
        self.v2_path = Path(v2_skills_path)
        self.v1_path = Path(v1_skills_path) if v1_skills_path else Path.home() / '.able' / 'skills'
        self.skills: Dict[str, Skill] = {}
        self.index_path = self.v2_path / 'SKILL_INDEX.yaml'

    def load_index(self):
        """Load skill index from disk"""
        # Load V2 index
        if self.index_path.exists():
            with open(self.index_path) as f:
                data = yaml.safe_load(f) or {}
                for name, skill_data in data.get('skills', {}).items():
                    try:
                        metadata = SkillMetadata.from_dict(skill_data)
                        skill_dir = self.v2_path / name
                        if skill_dir.exists():
                            impl_path = self._find_implementation(skill_dir)
                            if impl_path:
                                self.skills[name] = Skill(
                                    metadata=metadata,
                                    implementation_path=impl_path,
                                    implementation_type=self._detect_type(impl_path),
                                    source="v2"
                                )
                    except Exception as e:
                        logger.warning(f"Failed to load skill {name}: {e}")

        # Load V1 index
        v1_index = self.v1_path / 'SKILL_INDEX.yaml'
        if v1_index.exists():
            with open(v1_index) as f:
                data = yaml.safe_load(f) or {}
                for name, skill_data in data.get('skills', {}).items():
                    if name in self.skills:
                        continue  # V2 takes precedence
                    try:
                        metadata = SkillMetadata(
                            name=name,
                            description=skill_data.get('description', ''),
                            trigger_phrases=[skill_data.get('trigger', '')] if skill_data.get('trigger') else [],
                            last_used=datetime.fromisoformat(skill_data['last_used']) if skill_data.get('last_used') else None,
                            use_count=skill_data.get('use_count', 0),
                        )
                        skill_dir = self.v1_path / name
                        if skill_dir.exists():
                            impl_path = self._find_implementation(skill_dir)
                            if impl_path:
                                self.skills[name] = Skill(
                                    metadata=metadata,
                                    implementation_path=impl_path,
                                    implementation_type=self._detect_type(impl_path),
                                    source="v1"
                                )
                    except Exception as e:
                        logger.warning(f"Failed to load v1 skill {name}: {e}")

        logger.info(f"Loaded {len(self.skills)} skills from registry")

    def save_index(self):
        """Save skill index to disk"""
        v2_skills = {
            name: skill.metadata.to_dict()
            for name, skill in self.skills.items()
            if skill.source == "v2"
        }

        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.index_path, 'w') as f:
            yaml.dump({'skills': v2_skills}, f, default_flow_style=False)

    def _find_implementation(self, skill_dir: Path) -> Optional[Path]:
        """Find the implementation file in a skill directory"""
        for name in ['implement.py', 'main.py', 'skill.py', 'implement.sh', 'main.sh']:
            path = skill_dir / name
            if path.exists():
                return path
        return None

    def _detect_type(self, path: Path) -> str:
        """Detect implementation type from file extension"""
        if path.suffix == '.py':
            return 'python'
        elif path.suffix in ('.sh', '.bash'):
            return 'bash'
        return 'unknown'

    def register(
        self,
        name: str,
        metadata: SkillMetadata,
        implementation: Callable | Path,
        source: str = "v2"
    ):
        """Register a new skill"""
        if callable(implementation):
            skill = Skill(
                metadata=metadata,
                implementation_path=Path(f"<callable:{name}>"),
                implementation_type="callable",
                callable=implementation,
                source=source
            )
        else:
            skill = Skill(
                metadata=metadata,
                implementation_path=Path(implementation),
                implementation_type=self._detect_type(Path(implementation)),
                source=source
            )

        self.skills[name] = skill
        logger.info(f"Registered skill: {name}")

    def unregister(self, name: str):
        """Remove a skill from the registry"""
        if name in self.skills:
            del self.skills[name]
            logger.info(f"Unregistered skill: {name}")

    def get(self, name: str) -> Optional[Skill]:
        """Get a skill by name"""
        return self.skills.get(name)

    def find_by_trigger(self, text: str) -> List[Skill]:
        """Find skills that match a trigger phrase"""
        matches = []
        text_lower = text.lower()

        for skill in self.skills.values():
            for trigger in skill.metadata.trigger_phrases:
                if trigger.lower() in text_lower:
                    matches.append(skill)
                    break

        return matches

    def find_by_file_pattern(self, filepath: str) -> List[Skill]:
        """Find skills that match a file pattern"""
        import fnmatch
        matches = []

        for skill in self.skills.values():
            for pattern in skill.metadata.file_patterns:
                if fnmatch.fnmatch(filepath, pattern):
                    matches.append(skill)
                    break

        return matches

    def get_scheduled_skills(self) -> List[Skill]:
        """Get skills with cron schedules"""
        return [
            skill for skill in self.skills.values()
            if skill.metadata.cron_schedule
        ]

    def list_all(self) -> List[SkillMetadata]:
        """List all registered skill metadata"""
        return [skill.metadata for skill in self.skills.values()]

    def update_usage(self, name: str):
        """Update usage statistics for a skill"""
        if name in self.skills:
            self.skills[name].metadata.last_used = datetime.utcnow()
            self.skills[name].metadata.use_count += 1
            self.save_index()

    def get_statistics(self) -> Dict:
        """Get registry statistics"""
        return {
            "total_skills": len(self.skills),
            "by_source": {
                "v1": sum(1 for s in self.skills.values() if s.source == "v1"),
                "v2": sum(1 for s in self.skills.values() if s.source == "v2"),
            },
            "by_type": {
                "python": sum(1 for s in self.skills.values() if s.implementation_type == "python"),
                "bash": sum(1 for s in self.skills.values() if s.implementation_type == "bash"),
                "callable": sum(1 for s in self.skills.values() if s.implementation_type == "callable"),
            },
            "scheduled": len(self.get_scheduled_skills()),
            "most_used": sorted(
                [(s.metadata.name, s.metadata.use_count) for s in self.skills.values()],
                key=lambda x: x[1],
                reverse=True
            )[:5]
        }
