"""
Buddy data model — agent companion that grows alongside the ABLE runtime.

Every stat, level, and evolution stage maps to a real system metric.
No fake numbers — the buddy IS the system's performance made tangible.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    AETHER = "aether"     # Secret — master/orchestrator archetype


STARTER_SPECIES = (
    Species.BLAZE,
    Species.WAVE,
    Species.ROOT,
    Species.SPARK,
    Species.PHANTOM,
)
HIDDEN_SIGNAL_SPECIES = Species.AETHER


SPECIES_META = {
    Species.BLAZE: {
        "emoji": "\U0001f525",
        "label": "Blaze",
        "desc": "Code-forged. Thinks in diffs.",
        "element": "Fire",
        "role": "Coder",
        "best_for": "coding, debugging, fixes, and tool-heavy execution",
        "abilities": ["fast diffs", "bug hunts", "tool chaining"],
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
        "element": "Water",
        "role": "Researcher",
        "best_for": "research, analysis, synthesis, and context gathering",
        "abilities": ["source mapping", "pattern synthesis", "broad recall"],
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
        "element": "Earth",
        "role": "Operator",
        "best_for": "deploys, infrastructure, automation, and steady mixed ops work",
        "abilities": ["deploy focus", "runtime stability", "systems upkeep"],
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
        "element": "Lightning",
        "role": "Creative",
        "best_for": "writing, messaging, concepting, and content work",
        "abilities": ["fast phrasing", "idea bursts", "content momentum"],
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
        "element": "Shadow",
        "role": "Defender",
        "best_for": "security reviews, audits, threat hunting, and hardening",
        "abilities": ["risk sense", "attack-path spotting", "defensive pressure"],
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
    Species.AETHER: {
        "emoji": "\U0001f409",
        "label": "Aether",
        "desc": "The hidden sixth signal. Orchestrates the whole field.",
        "element": "Dragon/Psychic",
        "role": "Orchestrator",
        "best_for": "cross-domain orchestration, synthesis, and mastery across the full ABLE stack",
        "abilities": ["team orchestration", "cross-domain mastery", "signal fusion"],
        "bonus_domains": ["coding", "research", "production", "creative", "security"],
        "art_stage1": [
            "   ╭◇╮   ",
            "  ╭┤◉├╮  ",
            "   ╰┬┬╯   ",
            "  ≋╱╲≋  ",
        ],
        "art_stage2": [
            "  ╭─◇─╮  ",
            " ╭┤ ✦ ├╮ ",
            " ││◉ ◉││ ",
            " ╰┤╱╲├╯ ",
            "  ≋╰╯≋  ",
        ],
        "art_stage3": [
            " ╭──◇◇──╮ ",
            "╭┤  ✦✦  ├╮",
            "││ ◉  ◉ ││",
            "││╭────╮││",
            "╰┤╱╲╱╲╱├╯",
            " ≋╰────╯≋ ",
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

SHINY_STARTER_ODDS = 128  # Cosmetic-only rare hatch chance.

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

LEGENDARY_TITLES = {
    Species.BLAZE: "Forge Sovereign",
    Species.WAVE: "Tide Oracle",
    Species.ROOT: "Ironroot Warden",
    Species.SPARK: "Storm Scribe",
    Species.PHANTOM: "Night Sentinel",
    Species.AETHER: "Prime Orchestrator",
}

LEGENDARY_REQUIREMENTS = {
    "min_stage": Stage.EVOLVED.value,
    "min_level": 40,
    "min_eval_passes": 25,
    "min_battles_won": 10,
    "min_battle_streak": 3,
    "min_distillation_pairs": 250,
    "min_evolution_deploys": 5,
    "description": (
        "Stage 3 + level 40 + 25 eval passes + 10 wins + 3-win streak "
        "+ 250 distillation pairs + 5 evolution deploys"
    ),
}

CATCH_PROGRESS_TARGET = 12
SECRET_SIGNAL_LEVEL = 50

BADGE_DEFS = {
    "starter-license": {
        "title": "Starter License",
        "description": "Choose your first buddy.",
    },
    "field-guide": {
        "title": "Field Guide",
        "description": "Catch at least 3 buddy species.",
    },
    "trainer": {
        "title": "Trainer",
        "description": "Earn your first real evolution or fully build one buddy.",
    },
    "full-dex": {
        "title": "Full Dex",
        "description": "Catch all 5 starter species.",
    },
    "sixth-signal": {
        "title": "Sixth Signal",
        "description": "Unlock the hidden orchestrator buddy.",
    },
    "evolution-league": {
        "title": "Evolution League",
        "description": "Evolve the full roster to Stage 3.",
    },
    "legendary-league": {
        "title": "Legendary League",
        "description": "Unlock legendary form on the full roster.",
    },
    "master-trainer": {
        "title": "Master Trainer",
        "description": "Reach level 40 with the full roster.",
    },
    "signal-crown": {
        "title": "Signal Crown",
        "description": "Fully evolve and level the hidden sixth signal to its final form.",
    },
}

OMNIDEX_EASTER_EGG = {
    "title": "Sixth Signal",
    "message": (
        "100% starter completion reached. Aether, the hidden sixth signal, "
        "has awakened as the orchestrator of the whole roster."
    ),
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
    is_shiny: bool = False
    legendary_title: str = ""
    legendary_unlocked_at: str = ""
    current_battle_streak: int = 0
    best_battle_streak: int = 0
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

    @property
    def is_legendary(self) -> bool:
        return bool(self.legendary_title)

    @property
    def rarity_label(self) -> str:
        if self.is_legendary and self.is_shiny:
            return "Legendary Shiny"
        if self.is_legendary:
            return "Legendary"
        if self.is_shiny:
            return "Shiny"
        return "Standard"

    @property
    def display_emoji(self) -> str:
        markers: list[str] = []
        if self.is_legendary:
            markers.append("\U0001f451")
        if self.is_shiny:
            markers.append("\u2728")
        markers.append(self.meta["emoji"])
        return " ".join(markers)

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

    def qualifies_for_legendary(self) -> bool:
        reqs = LEGENDARY_REQUIREMENTS
        return (
            self.stage >= reqs["min_stage"]
            and self.level >= reqs["min_level"]
            and self.eval_passes >= reqs["min_eval_passes"]
            and self.battles_won >= reqs["min_battles_won"]
            and self.best_battle_streak >= reqs["min_battle_streak"]
            and self.distillation_pairs >= reqs["min_distillation_pairs"]
            and self.evolution_deploys >= reqs["min_evolution_deploys"]
        )

    def unlock_legendary(self) -> Optional[str]:
        """Promote the buddy into an earned legendary form once milestones are met."""
        if self.is_legendary or not self.qualifies_for_legendary():
            return None
        self.legendary_title = LEGENDARY_TITLES[self.species_enum]
        self.legendary_unlocked_at = datetime.now(timezone.utc).isoformat()
        return self.legendary_title

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
            self.current_battle_streak += 1
            self.best_battle_streak = max(self.best_battle_streak, self.current_battle_streak)
        elif record.result == "loss":
            self.battles_lost += 1
            self.current_battle_streak = 0
        else:
            self.battles_drawn += 1
            self.current_battle_streak = 0
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
        self.unlock_legendary()


@dataclass
class BuddyCollection:
    """Persistent collection of caught buddies with one active party member."""

    active_species: str = ""
    buddies: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    catch_progress: Dict[str, int] = field(default_factory=dict)
    operator_profile: Dict[str, str] = field(default_factory=dict)
    badges: List[Dict[str, str]] = field(default_factory=list)
    easter_egg_title: str = ""
    easter_egg_message: str = ""
    easter_egg_unlocked_at: str = ""

    def list_buddies(self) -> List[BuddyState]:
        order = {species.value: idx for idx, species in enumerate(Species)}
        buddies = [_deserialize_buddy(data) for data in self.buddies.values()]
        return sorted(
            [buddy for buddy in buddies if buddy is not None],
            key=lambda buddy: order.get(buddy.species, 999),
        )

    def get_active_buddy(self) -> Optional[BuddyState]:
        if self.active_species and self.active_species in self.buddies:
            return _deserialize_buddy(self.buddies[self.active_species])
        buddies = self.list_buddies()
        if buddies:
            self.active_species = buddies[0].species
            return buddies[0]
        return None

    def upsert_buddy(self, buddy: BuddyState, *, make_active: bool = False) -> None:
        self.buddies[buddy.species] = _serialize_buddy(buddy)
        self.catch_progress.setdefault(buddy.species, CATCH_PROGRESS_TARGET)
        if make_active or not self.active_species:
            self.active_species = buddy.species

    def get_progress(self, species: Species) -> int:
        return int(self.catch_progress.get(species.value, 0))

    def badge_ids(self) -> set[str]:
        return {badge.get("id", "") for badge in self.badges}


def _starter_buddies(buddies: List[BuddyState]) -> List[BuddyState]:
    starter_ids = {species.value for species in STARTER_SPECIES}
    return [buddy for buddy in buddies if buddy.species in starter_ids]


def _secret_signal_buddy(buddies: List[BuddyState]) -> Optional[BuddyState]:
    for buddy in buddies:
        if buddy.species == HIDDEN_SIGNAL_SPECIES.value:
            return buddy
    return None


def starter_is_shiny(
    *,
    name: str,
    species: Species,
    created_at: str,
    odds: Optional[int] = None,
) -> bool:
    """Stable shiny roll so the same starter seed always yields the same cosmetic variant."""
    odds = SHINY_STARTER_ODDS if odds is None else odds
    if odds <= 1:
        return True
    seed = f"{species.value}:{name.strip().lower()}:{created_at.strip()}"
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % odds == 0


def create_starter_buddy(
    *,
    name: str,
    species: Species,
    catch_phrase: str = "",
    created_at: str = "",
) -> BuddyState:
    """Create a starter buddy with deterministic rarity metadata."""
    created = created_at or datetime.now(timezone.utc).isoformat()
    return BuddyState(
        name=name,
        species=species.value,
        stage=Stage.STARTER.value,
        xp=0,
        catch_phrase=catch_phrase or SPECIES_META[species]["desc"],
        created_at=created,
        is_shiny=starter_is_shiny(name=name, species=species, created_at=created),
    )


def create_hidden_signal_buddy(*, created_at: str = "") -> BuddyState:
    """Create the hidden orchestrator buddy unlocked from full completion."""
    created = created_at or datetime.now(timezone.utc).isoformat()
    return BuddyState(
        name=SPECIES_META[HIDDEN_SIGNAL_SPECIES]["label"],
        species=HIDDEN_SIGNAL_SPECIES.value,
        stage=Stage.STARTER.value,
        xp=0,
        catch_phrase="Master of the full signal field.",
        created_at=created,
        is_shiny=False,
    )


# ── Persistence ──────────────────────────────────────────────────────────

BUDDY_PATH = Path.home() / ".able" / "buddy.yaml"
BUDDY_COLLECTION_PATH = Path.home() / ".able" / "buddy_collection.yaml"

def _serialize_buddy(buddy: BuddyState) -> Dict[str, Any]:
    return {
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
        "is_shiny": buddy.is_shiny,
        "legendary_title": buddy.legendary_title,
        "legendary_unlocked_at": buddy.legendary_unlocked_at,
        "current_battle_streak": buddy.current_battle_streak,
        "best_battle_streak": buddy.best_battle_streak,
        "needs_hunger": buddy.needs_hunger,
        "needs_thirst": buddy.needs_thirst,
        "needs_energy": buddy.needs_energy,
        "needs_last_decay": buddy.needs_last_decay,
        "domains_used_today": buddy.domains_used_today,
        "domains_today_date": buddy.domains_today_date,
    }


def _deserialize_buddy(data: Dict[str, Any] | None) -> Optional[BuddyState]:
    try:
        if not data or not isinstance(data, dict):
            return None
        return BuddyState(**{
            k: v for k, v in data.items()
            if k in BuddyState.__dataclass_fields__
        })
    except Exception:
        return None


def _load_legacy_buddy(path: Path | None = None) -> Optional[BuddyState]:
    path = path or BUDDY_PATH
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return _deserialize_buddy(data)


def _unlock_badge(collection: BuddyCollection, badge_id: str) -> Optional[Dict[str, str]]:
    if badge_id in collection.badge_ids():
        return None
    badge_def = BADGE_DEFS[badge_id]
    badge = {
        "id": badge_id,
        "title": badge_def["title"],
        "description": badge_def["description"],
        "unlocked_at": datetime.now(timezone.utc).isoformat(),
    }
    collection.badges.append(badge)
    return badge


def _refresh_collection_rewards(collection: BuddyCollection) -> Dict[str, Any]:
    buddies = collection.list_buddies()
    starter_buddies = _starter_buddies(buddies)
    starter_total = len(STARTER_SPECIES)
    secret_buddy = _secret_signal_buddy(buddies)
    new_badges: List[Dict[str, str]] = []
    new_buddies: List[BuddyState] = []

    if len(buddies) >= 1:
        badge = _unlock_badge(collection, "starter-license")
        if badge:
            new_badges.append(badge)
    if any(b.stage >= Stage.TRAINED.value or b.level >= Stage.EVOLVED.value for b in buddies):
        badge = _unlock_badge(collection, "trainer")
        if badge:
            new_badges.append(badge)
    if len(starter_buddies) >= 3:
        badge = _unlock_badge(collection, "field-guide")
        if badge:
            new_badges.append(badge)
    if len(starter_buddies) == starter_total:
        badge = _unlock_badge(collection, "full-dex")
        if badge:
            new_badges.append(badge)
    if (
        len(starter_buddies) == starter_total
        and all(b.stage >= Stage.EVOLVED.value for b in starter_buddies)
    ):
        badge = _unlock_badge(collection, "evolution-league")
        if badge:
            new_badges.append(badge)
    if (
        len(starter_buddies) == starter_total
        and all(b.is_legendary for b in starter_buddies)
    ):
        badge = _unlock_badge(collection, "legendary-league")
        if badge:
            new_badges.append(badge)
    if (
        len(starter_buddies) == starter_total
        and all(b.level >= LEGENDARY_REQUIREMENTS["min_level"] for b in starter_buddies)
    ):
        badge = _unlock_badge(collection, "master-trainer")
        if badge:
            new_badges.append(badge)

    easter_egg_unlocked = False
    if (
        len(starter_buddies) == starter_total
        and all(b.stage >= Stage.EVOLVED.value for b in starter_buddies)
        and all(b.is_legendary for b in starter_buddies)
        and all(b.level >= LEGENDARY_REQUIREMENTS["min_level"] for b in starter_buddies)
        and secret_buddy is None
    ):
        secret_buddy = create_hidden_signal_buddy(
            created_at=datetime.now(timezone.utc).isoformat()
        )
        collection.upsert_buddy(secret_buddy, make_active=False)
        new_buddies.append(secret_buddy)
        badge = _unlock_badge(collection, "sixth-signal")
        if badge:
            new_badges.append(badge)
        collection.easter_egg_title = OMNIDEX_EASTER_EGG["title"]
        collection.easter_egg_message = OMNIDEX_EASTER_EGG["message"]
        collection.easter_egg_unlocked_at = datetime.now(timezone.utc).isoformat()
        easter_egg_unlocked = True
    elif secret_buddy and not collection.easter_egg_title:
        collection.easter_egg_title = OMNIDEX_EASTER_EGG["title"]
        collection.easter_egg_message = OMNIDEX_EASTER_EGG["message"]
        collection.easter_egg_unlocked_at = collection.easter_egg_unlocked_at or datetime.now(timezone.utc).isoformat()

    if (
        secret_buddy
        and secret_buddy.stage >= Stage.EVOLVED.value
        and secret_buddy.level >= SECRET_SIGNAL_LEVEL
        and secret_buddy.is_legendary
    ):
        badge = _unlock_badge(collection, "signal-crown")
        if badge:
            new_badges.append(badge)

    return {
        "new_buddies": new_buddies,
        "new_badges": new_badges,
        "easter_egg_unlocked": easter_egg_unlocked,
    }


def load_buddy_collection() -> Optional[BuddyCollection]:
    """Load the buddy collection, migrating from the legacy single-buddy file if needed."""
    if BUDDY_COLLECTION_PATH.exists():
        try:
            raw = yaml.safe_load(BUDDY_COLLECTION_PATH.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {}
        collection = BuddyCollection(
            active_species=str(raw.get("active_species", "")),
            buddies={
                str(species): data
                for species, data in (raw.get("buddies") or {}).items()
                if isinstance(data, dict)
            },
            catch_progress={
                str(species): int(value)
                for species, value in (raw.get("catch_progress") or {}).items()
            },
            operator_profile={
                str(key): str(value)
                for key, value in (raw.get("operator_profile") or {}).items()
            },
            badges=list(raw.get("badges") or []),
            easter_egg_title=str(raw.get("easter_egg_title", "")),
            easter_egg_message=str(raw.get("easter_egg_message", "")),
            easter_egg_unlocked_at=str(raw.get("easter_egg_unlocked_at", "")),
        )
        if not collection.active_species:
            active = collection.get_active_buddy()
            if active:
                collection.active_species = active.species
        return collection

    legacy = _load_legacy_buddy()
    if legacy is None:
        return None
    collection = BuddyCollection(active_species=legacy.species)
    collection.upsert_buddy(legacy, make_active=True)
    _refresh_collection_rewards(collection)
    save_buddy_collection(collection)
    return collection


def save_buddy_collection(collection: BuddyCollection) -> None:
    """Persist the collection and keep the legacy active-buddy mirror in sync."""
    BUDDY_COLLECTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "active_species": collection.active_species,
        "buddies": collection.buddies,
        "catch_progress": collection.catch_progress,
        "operator_profile": collection.operator_profile,
        "badges": collection.badges,
        "easter_egg_title": collection.easter_egg_title,
        "easter_egg_message": collection.easter_egg_message,
        "easter_egg_unlocked_at": collection.easter_egg_unlocked_at,
    }
    BUDDY_COLLECTION_PATH.write_text(
        yaml.dump(payload, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    active = collection.get_active_buddy()
    if active is not None:
        BUDDY_PATH.write_text(
            yaml.dump(_serialize_buddy(active), default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )


def list_buddies() -> List[BuddyState]:
    collection = load_buddy_collection()
    return collection.list_buddies() if collection else []


def switch_active_buddy(selector: str) -> Optional[BuddyState]:
    """Switch the active buddy by species, label, or chosen name."""
    collection = load_buddy_collection()
    if collection is None:
        return None
    normalized = selector.strip().lower()
    for buddy in collection.list_buddies():
        if normalized in {buddy.species, buddy.meta["label"].lower(), buddy.name.lower()}:
            collection.active_species = buddy.species
            save_buddy_collection(collection)
            return buddy
    return None


def update_collection_profile(profile: Dict[str, str]) -> BuddyCollection:
    """Persist operator-facing buddy onboarding preferences."""
    collection = load_buddy_collection() or BuddyCollection()
    cleaned = {
        str(key): str(value).strip()
        for key, value in profile.items()
        if str(value).strip()
    }
    collection.operator_profile.update(cleaned)
    save_buddy_collection(collection)
    return collection


def reset_buddy_collection() -> None:
    """Delete buddy persistence when the operator needs to re-run starter setup."""
    for path in (BUDDY_PATH, BUDDY_COLLECTION_PATH):
        try:
            if path.exists():
                path.unlink()
        except OSError:
            continue


def record_collection_progress(domain: str, *, points: int = 1) -> Dict[str, Any]:
    """Advance catch progress for uncaught species tied to the domain worked on."""
    collection = load_buddy_collection()
    if collection is None or not domain or domain == "default":
        return {"new_buddies": [], "new_badges": [], "easter_egg_unlocked": False}

    new_buddies: List[BuddyState] = []
    for species in STARTER_SPECIES:
        meta = SPECIES_META[species]
        if domain not in meta.get("bonus_domains", []):
            continue
        current = collection.catch_progress.get(species.value, 0)
        collection.catch_progress[species.value] = current + max(points, 0)
        if species.value not in collection.buddies and collection.catch_progress[species.value] >= CATCH_PROGRESS_TARGET:
            new_buddy = create_starter_buddy(
                name=meta["label"],
                species=species,
                catch_phrase=f"Caught through {domain} work.",
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            collection.upsert_buddy(new_buddy, make_active=False)
            new_buddies.append(new_buddy)

    reward_update = _refresh_collection_rewards(collection)
    save_buddy_collection(collection)
    return {
        "new_buddies": new_buddies + reward_update.get("new_buddies", []),
        "new_badges": reward_update["new_badges"],
        "easter_egg_unlocked": reward_update["easter_egg_unlocked"],
    }


def load_buddy() -> Optional[BuddyState]:
    """Load the active buddy from disk. Returns None if none has been created."""
    collection = load_buddy_collection()
    if collection is not None:
        return collection.get_active_buddy()
    return _load_legacy_buddy()


def save_buddy(buddy: BuddyState) -> None:
    """Persist the active buddy and synchronize the collection state."""
    collection = load_buddy_collection() or BuddyCollection()
    make_active = not collection.active_species or collection.active_species == buddy.species
    collection.upsert_buddy(buddy, make_active=make_active)
    _refresh_collection_rewards(collection)
    save_buddy_collection(collection)
