"""
Buddy data model — agent companion that grows alongside the ABLE runtime.

Every stat, level, and evolution stage maps to a real system metric.
No fake numbers — the buddy IS the system's performance made tangible.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


# ── Species (starter archetypes) ─────────────────────────────────────────

class Species(Enum):
    """Starter archetypes — each biases which domains earn bonus XP."""

    BLAZE = "blaze"       # Coder — code, debugging, tool execution
    WAVE = "wave"         # Researcher — web research, analysis, synthesis
    ROOT = "root"         # Builder — deploy, infra, automation
    SPARK = "spark"       # Creative — copywriting, design, content
    PHANTOM = "phantom"   # Security — audits, threat analysis, hardening


SPECIES_META = {
    Species.BLAZE: {
        "emoji": "\U0001f525",
        "label": "Blaze",
        "desc": "Code-forged. Thinks in diffs.",
        "bonus_domains": ["coding", "debugging", "code"],
        "art_stage1": [
            "  ╭──╮ ",
            "  │◉◉│ ",
            "  ╰┬┬╯ ",
            "  ╱╲╱╲ ",
        ],
        "art_stage2": [
            " ╭────╮",
            " │✦  ✦│",
            " ╰┬──┬╯",
            " ╱╲╱╲╱╲",
            "  ╰──╯ ",
        ],
        "art_stage3": [
            "╭──────╮",
            "│ ✦✦✦✦ │",
            "│ ◉  ◉ │",
            "╰┬────┬╯",
            "╱╲╱╲╱╲╱╲",
            " ╰────╯ ",
        ],
    },
    Species.WAVE: {
        "emoji": "\U0001f30a",
        "label": "Wave",
        "desc": "Reads everything. Synthesizes fast.",
        "bonus_domains": ["research", "analysis", "data"],
        "art_stage1": [
            "  ~≈~  ",
            "  │○○│ ",
            "  ╰╮╭╯ ",
            "  ≈≈≈≈ ",
        ],
        "art_stage2": [
            " ~≈≈≈~ ",
            " │ ○○ │",
            " ╰╮╭╮╭╯",
            " ≈≈≈≈≈≈",
            "  ╰╮╭╯ ",
        ],
        "art_stage3": [
            "~≈≈≈≈≈~",
            "│ ✦  ✦ │",
            "│  ○○  │",
            "╰╮╭╮╭╮╭╯",
            "≈≈≈≈≈≈≈≈",
            " ╰╮╭╮╭╯",
        ],
    },
    Species.ROOT: {
        "emoji": "\U0001f331",
        "label": "Root",
        "desc": "Ships to prod. Keeps it running.",
        "bonus_domains": ["production", "infrastructure", "deploy"],
        "art_stage1": [
            "  ╭▲╮  ",
            "  │··│  ",
            "  ╰┤├╯  ",
            "  ╱╲╱╲  ",
        ],
        "art_stage2": [
            " ╭─▲─╮ ",
            " │ ·· │ ",
            " ╰┤──├╯ ",
            " ╱╲╱╲╱╲ ",
            "  ╰──╯  ",
        ],
        "art_stage3": [
            "╭──▲▲──╮",
            "│  ··  │",
            "│ ╭──╮ │",
            "╰┤╱╲╱├╯ ",
            "╱╲╱╲╱╲╱╲",
            " ╰────╯ ",
        ],
    },
    Species.SPARK: {
        "emoji": "\u26a1",
        "label": "Spark",
        "desc": "Words are weapons. Ideas are ammo.",
        "bonus_domains": ["creative", "copywriting", "content"],
        "art_stage1": [
            "  ╭★╮  ",
            "  │@@│  ",
            "  ╰┬┬╯  ",
            "   ⚡   ",
        ],
        "art_stage2": [
            " ╭─★─╮ ",
            " │ @@ │ ",
            " ╰┬──┬╯ ",
            "  ⚡⚡⚡  ",
            "   ╰╯   ",
        ],
        "art_stage3": [
            "╭──★★──╮",
            "│  @@  │",
            "│ ╭──╮ │",
            "╰┬⚡⚡⚡┬╯",
            " ⚡⚡⚡⚡⚡ ",
            "  ╰──╯  ",
        ],
    },
    Species.PHANTOM: {
        "emoji": "\U0001f47b",
        "label": "Phantom",
        "desc": "Finds the cracks. Seals them shut.",
        "bonus_domains": ["security", "audit", "threat"],
        "art_stage1": [
            "  ╭~~╮  ",
            "  │°°│  ",
            "  ╰╮╭╯  ",
            "   ~~   ",
        ],
        "art_stage2": [
            " ╭~──~╮ ",
            " │ °° │ ",
            " ╰╮╭╮╭╯ ",
            "  ~~~~~  ",
            "   ╰╯   ",
        ],
        "art_stage3": [
            "╭~~──~~╮",
            "│  °°  │",
            "│ ╭~~╮ │",
            "╰╮╭╮╭╮╭╯",
            " ~~~~~~~ ",
            "  ╰~~╯  ",
        ],
    },
}


# ── Evolution stages ─────────────────────────────────────────────────────

class Stage(Enum):
    """Evolution stages — triggered by real system milestones."""

    STARTER = 1     # Fresh agent, learning the ropes
    TRAINED = 2     # Routing tuned, evals passing, skills sharpening
    EVOLVED = 3     # Self-tuning active, distilled models deployed


STAGE_NAMES = {
    Stage.STARTER: "Starter",
    Stage.TRAINED: "Trained",
    Stage.EVOLVED: "Evolved",
}

# Real milestones that trigger evolution
EVOLUTION_REQUIREMENTS = {
    Stage.TRAINED: {
        "min_level": 10,
        "min_interactions": 50,
        "min_eval_passes": 5,
        "description": "50 interactions + 5 eval passes + level 10",
    },
    Stage.EVOLVED: {
        "min_level": 25,
        "min_distillation_pairs": 100,
        "min_evolution_deploys": 3,
        "description": "100 distillation pairs + 3 evolution deploys + level 25",
    },
}


# ── XP and leveling ─────────────────────────────────────────────────────

# XP = complexity_score * base_multiplier + bonuses
XP_PER_INTERACTION = 10         # Base XP per interaction
XP_COMPLEXITY_MULTIPLIER = 20   # Extra XP per 0.1 complexity score
XP_TOOL_EXECUTION = 5           # Bonus for tool use
XP_APPROVAL_GRANTED = 8         # Bonus for approved write action
XP_BATTLE_WIN = 50              # Eval battle win
XP_BATTLE_DRAW = 20             # Eval battle draw
XP_DOMAIN_BONUS = 5             # Bonus when interaction matches species domain


def xp_for_level(level: int) -> int:
    """Total XP required to reach a given level. Quadratic curve."""
    return int(50 * level * (level + 1) / 2)


def level_from_xp(total_xp: int) -> int:
    """Derive current level from total XP."""
    level = 1
    while xp_for_level(level + 1) <= total_xp:
        level += 1
    return min(level, 100)


# ── Stats (derived from real metrics) ────────────────────────────────────

@dataclass
class BuddyStats:
    """Live stats derived from interaction log metrics. Not stored — computed."""

    accuracy: float = 0.0    # Routing accuracy (right tier for job)
    speed: float = 0.0       # Avg response latency score (inverse)
    resilience: float = 0.0  # Fallback recovery rate
    wisdom: float = 0.0      # Memory recall relevance
    evolution: float = 0.0   # Self-improvement cycle impact

    def as_dict(self) -> Dict[str, float]:
        return {
            "ACC": round(self.accuracy, 1),
            "SPD": round(self.speed, 1),
            "RES": round(self.resilience, 1),
            "WIS": round(self.wisdom, 1),
            "EVO": round(self.evolution, 1),
        }

    def stat_bar(self, label: str, value: float, width: int = 10) -> str:
        filled = int(value / 100 * width)
        empty = width - filled
        return f"{label}:{'\u2588' * filled}{'\u2591' * empty} {value:.0f}"


# ── Needs (Tamagotchi layer — maps to real maintenance) ──────────────────

# Each need decays toward 0 over time if not tended.
# Need values: 0-100.  Below 30 = critical, below 60 = warning.
#
# Mapping to real system actions:
#   HUNGER  → Feed it by running evals / battles (training data)
#   THIRST  → Water it by running evolution cycles (weight adaptation)
#   ENERGY  → Walk it by exploring new domains / using varied tools
#
# Decay rates (points lost per hour of inactivity):
NEED_DECAY_PER_HOUR = {
    "hunger": 2.0,    # Needs feeding every ~24h to stay above 50
    "thirst": 1.5,    # Needs watering every ~24h
    "energy": 1.0,    # Needs walking every ~36h
}

# Restoration amounts per action:
NEED_RESTORE = {
    "hunger": {
        "battle": 30,      # Running an eval battle feeds it well
        "eval_pass": 10,   # Each eval pass is a snack
    },
    "thirst": {
        "evolve": 40,      # Running evolution cycle = big drink
        "interaction": 3,  # Each interaction is a sip
    },
    "energy": {
        "new_domain": 25,  # Using a domain for the first time today
        "tool_use": 5,     # Using a tool = a short walk
        "variety": 15,     # Using 3+ different domains in a session
    },
}

MOOD_THRESHOLDS = {
    "thriving": 70,   # All needs above 70
    "content": 50,    # All needs above 50
    "hungry": 30,     # Any need below 30
    "neglected": 10,  # Any need below 10
}

MOOD_MESSAGES = {
    "thriving": [
        "Feeling great!",
        "Ready for anything.",
        "Peak performance.",
    ],
    "content": [
        "Doing okay.",
        "Could use some attention.",
        "Steady.",
    ],
    "hungry": [
        "Getting hungry... run some evals?",
        "Thirsty... time for an evolution cycle?",
        "Restless... try a new domain?",
    ],
    "neglected": [
        "Help... needs attention...",
        "Running on empty.",
        "Please come back...",
    ],
}


@dataclass
class BuddyNeeds:
    """
    Virtual pet needs — each maps to a real maintenance action.

    Decays over time.  Restored by the actions they represent.
    """

    hunger: float = 80.0   # Fed by evals/battles
    thirst: float = 80.0   # Watered by evolution cycles
    energy: float = 80.0   # Walked by domain exploration
    last_decay_at: str = ""  # ISO timestamp of last decay calculation

    def decay(self, hours_elapsed: float) -> None:
        """Apply time-based decay to all needs."""
        self.hunger = max(0, self.hunger - NEED_DECAY_PER_HOUR["hunger"] * hours_elapsed)
        self.thirst = max(0, self.thirst - NEED_DECAY_PER_HOUR["thirst"] * hours_elapsed)
        self.energy = max(0, self.energy - NEED_DECAY_PER_HOUR["energy"] * hours_elapsed)

    def feed(self, action: str = "battle") -> float:
        """Restore hunger.  Returns amount restored."""
        amount = NEED_RESTORE["hunger"].get(action, 5)
        old = self.hunger
        self.hunger = min(100, self.hunger + amount)
        return self.hunger - old

    def water(self, action: str = "interaction") -> float:
        """Restore thirst.  Returns amount restored."""
        amount = NEED_RESTORE["thirst"].get(action, 3)
        old = self.thirst
        self.thirst = min(100, self.thirst + amount)
        return self.thirst - old

    def walk(self, action: str = "tool_use") -> float:
        """Restore energy.  Returns amount restored."""
        amount = NEED_RESTORE["energy"].get(action, 5)
        old = self.energy
        self.energy = min(100, self.energy + amount)
        return self.energy - old

    @property
    def mood(self) -> str:
        """Derive mood from current need levels."""
        lowest = min(self.hunger, self.thirst, self.energy)
        if lowest < MOOD_THRESHOLDS["neglected"]:
            return "neglected"
        if lowest < MOOD_THRESHOLDS["hungry"]:
            return "hungry"
        if lowest < MOOD_THRESHOLDS["thriving"]:
            return "content"
        return "thriving"

    @property
    def mood_message(self) -> str:
        import random
        messages = MOOD_MESSAGES.get(self.mood, ["..."])
        return random.choice(messages)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "hunger": round(self.hunger, 1),
            "thirst": round(self.thirst, 1),
            "energy": round(self.energy, 1),
            "mood": self.mood,
        }


# ── Battle record ────────────────────────────────────────────────────────

@dataclass
class BattleRecord:
    """Result of an eval-based battle."""

    domain: str
    score_pct: float
    passed: int
    total: int
    result: str  # "win", "draw", "loss"
    xp_earned: int
    timestamp: str


# ── Core buddy state ─────────────────────────────────────────────────────

@dataclass
class BuddyState:
    """
    Persistent buddy state.  Stored in ~/.able/buddy.yaml.

    Everything here is either operator-chosen (name, species) or
    accumulated from real system activity (xp, battles, milestones).
    """

    name: str = ""
    species: str = "blaze"
    stage: int = 1
    xp: int = 0
    battles_won: int = 0
    battles_lost: int = 0
    battles_drawn: int = 0
    total_interactions: int = 0
    eval_passes: int = 0
    evolution_deploys: int = 0
    distillation_pairs: int = 0
    battle_log: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    catch_phrase: str = ""
    # Needs (Tamagotchi layer)
    needs_hunger: float = 80.0
    needs_thirst: float = 80.0
    needs_energy: float = 80.0
    needs_last_decay: str = ""
    domains_used_today: List[str] = field(default_factory=list)
    domains_today_date: str = ""

    @property
    def level(self) -> int:
        return level_from_xp(self.xp)

    @property
    def xp_to_next(self) -> int:
        return xp_for_level(self.level + 1) - self.xp

    @property
    def xp_progress_pct(self) -> float:
        current_floor = xp_for_level(self.level)
        next_floor = xp_for_level(self.level + 1)
        span = max(next_floor - current_floor, 1)
        return min(100.0, (self.xp - current_floor) / span * 100)

    @property
    def species_enum(self) -> Species:
        return Species(self.species)

    @property
    def stage_enum(self) -> Stage:
        return Stage(self.stage)

    @property
    def meta(self) -> Dict[str, Any]:
        return SPECIES_META[self.species_enum]

    def check_evolution(self) -> Optional[Stage]:
        """Check if the buddy qualifies for the next evolution stage."""
        current = self.stage_enum
        if current == Stage.EVOLVED:
            return None  # Max stage

        next_stage = Stage(current.value + 1)
        reqs = EVOLUTION_REQUIREMENTS[next_stage]

        if self.level < reqs["min_level"]:
            return None

        if next_stage == Stage.TRAINED:
            if (self.total_interactions >= reqs["min_interactions"]
                    and self.eval_passes >= reqs["min_eval_passes"]):
                return next_stage
        elif next_stage == Stage.EVOLVED:
            if (self.distillation_pairs >= reqs["min_distillation_pairs"]
                    and self.evolution_deploys >= reqs["min_evolution_deploys"]):
                return next_stage
        return None

    def evolve(self, to_stage: Stage) -> None:
        self.stage = to_stage.value

    def award_xp(self, amount: int) -> int:
        """Award XP and return new level (may have leveled up)."""
        old_level = self.level
        self.xp += amount
        return self.level

    def get_needs(self) -> BuddyNeeds:
        """Build a BuddyNeeds view from stored fields."""
        return BuddyNeeds(
            hunger=self.needs_hunger,
            thirst=self.needs_thirst,
            energy=self.needs_energy,
            last_decay_at=self.needs_last_decay,
        )

    def _sync_needs(self, needs: BuddyNeeds) -> None:
        """Write BuddyNeeds back into stored fields."""
        self.needs_hunger = needs.hunger
        self.needs_thirst = needs.thirst
        self.needs_energy = needs.energy
        self.needs_last_decay = needs.last_decay_at

    def apply_needs_decay(self) -> str:
        """Apply time-based decay since last check. Returns mood."""
        from datetime import datetime, timezone

        needs = self.get_needs()
        now = datetime.now(timezone.utc)

        if needs.last_decay_at:
            try:
                last = datetime.fromisoformat(needs.last_decay_at)
                hours = (now - last).total_seconds() / 3600
                if hours > 0:
                    needs.decay(hours)
            except (ValueError, TypeError):
                pass

        needs.last_decay_at = now.isoformat()
        self._sync_needs(needs)
        return needs.mood

    def feed(self, action: str = "battle") -> float:
        """Feed the buddy (evals/battles). Returns amount restored."""
        needs = self.get_needs()
        restored = needs.feed(action)
        self._sync_needs(needs)
        return restored

    def water(self, action: str = "interaction") -> float:
        """Water the buddy (evolution/interactions). Returns amount restored."""
        needs = self.get_needs()
        restored = needs.water(action)
        self._sync_needs(needs)
        return restored

    def walk(self, action: str = "tool_use", domain: str = "") -> float:
        """Walk the buddy (domain exploration/tool use). Returns amount restored."""
        from datetime import date

        today = date.today().isoformat()
        if self.domains_today_date != today:
            self.domains_used_today = []
            self.domains_today_date = today

        needs = self.get_needs()

        if domain and domain not in self.domains_used_today:
            self.domains_used_today.append(domain)
            restored = needs.walk("new_domain")
            if len(self.domains_used_today) >= 3:
                needs.walk("variety")
        else:
            restored = needs.walk(action)

        self._sync_needs(needs)
        return restored

    @property
    def mood(self) -> str:
        return self.get_needs().mood

    @property
    def mood_message(self) -> str:
        return self.get_needs().mood_message

    def record_battle(self, record: BattleRecord) -> None:
        if record.result == "win":
            self.battles_won += 1
        elif record.result == "loss":
            self.battles_lost += 1
        else:
            self.battles_drawn += 1
        self.xp += record.xp_earned
        self.battle_log.append({
            "domain": record.domain,
            "score": record.score_pct,
            "result": record.result,
            "xp": record.xp_earned,
            "at": record.timestamp,
        })
        # Keep log bounded
        if len(self.battle_log) > 50:
            self.battle_log = self.battle_log[-50:]


# ── Persistence ──────────────────────────────────────────────────────────

BUDDY_PATH = Path.home() / ".able" / "buddy.yaml"


def load_buddy() -> Optional[BuddyState]:
    """Load buddy state from disk.  Returns None if not yet created."""
    if not BUDDY_PATH.exists():
        return None
    try:
        data = yaml.safe_load(BUDDY_PATH.read_text(encoding="utf-8"))
        if not data or not isinstance(data, dict):
            return None
        return BuddyState(**{
            k: v for k, v in data.items()
            if k in BuddyState.__dataclass_fields__
        })
    except Exception:
        return None


def save_buddy(buddy: BuddyState) -> None:
    """Persist buddy state to disk."""
    BUDDY_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "name": buddy.name,
        "species": buddy.species,
        "stage": buddy.stage,
        "xp": buddy.xp,
        "battles_won": buddy.battles_won,
        "battles_lost": buddy.battles_lost,
        "battles_drawn": buddy.battles_drawn,
        "total_interactions": buddy.total_interactions,
        "eval_passes": buddy.eval_passes,
        "evolution_deploys": buddy.evolution_deploys,
        "distillation_pairs": buddy.distillation_pairs,
        "battle_log": buddy.battle_log,
        "created_at": buddy.created_at,
        "catch_phrase": buddy.catch_phrase,
        "needs_hunger": buddy.needs_hunger,
        "needs_thirst": buddy.needs_thirst,
        "needs_energy": buddy.needs_energy,
        "needs_last_decay": buddy.needs_last_decay,
        "domains_used_today": buddy.domains_used_today,
        "domains_today_date": buddy.domains_today_date,
    }
    BUDDY_PATH.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
