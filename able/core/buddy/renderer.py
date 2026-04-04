"""
Buddy CLI renderer — ASCII art display for the terminal.

Shows the buddy's current state, stats, and evolution stage in `able chat`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from .model import (
    BuddyState,
    BuddyCollection,
    BuddyStats,
    BuddyNeeds,
    SPECIES_META,
    STAGE_NAMES,
    EVOLUTION_REQUIREMENTS,
    LEGENDARY_REQUIREMENTS,
    CATCH_PROGRESS_TARGET,
    STARTER_SPECIES,
    HIDDEN_SIGNAL_SPECIES,
    SECRET_SIGNAL_LEVEL,
    Stage,
    Species,
)

# ── Color support ─────────────────────────────────────────────────────────────

class _ColorFlag:
    """Lazy color detection — re-evaluates on every bool() check.

    Module-level detection is unreliable: the module might be imported during
    async startup, a background thread, or a non-TTY subprocess before the
    interactive terminal is fully attached.  This class always reflects the
    *current* state of sys.stdout so colors work correctly wherever the render
    functions are actually called from.
    """

    def __bool__(self) -> bool:
        return (
            not os.environ.get("NO_COLOR")
            and hasattr(sys.stdout, "isatty")
            and bool(sys.stdout.isatty())
        )

    def __repr__(self) -> str:
        return f"_ColorFlag(active={bool(self)})"


_COLORS_ON = _ColorFlag()

_RESET = "\033[0m"
_BOLD  = "\033[1m"
_DIM   = "\033[2m"
_ANSI_ESCAPE = re.compile(r"\033\[[^m]*m")


def _rgb(r: int, g: int, b: int) -> str:
    """Truecolor (24-bit) foreground escape."""
    return f"\033[38;2;{r};{g};{b}m"


def _c(code: str, text: str) -> str:
    """Apply ANSI code + reset if color is on."""
    return f"{code}{text}{_RESET}" if _COLORS_ON else text


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences for width calculation."""
    return _ANSI_ESCAPE.sub("", text)


# Named colour palette
_GOLD_BRIGHT  = _rgb(255, 215,   0)  # #FFD700 — filled XP, shimmer peak
_GOLD         = _rgb(212, 175,  55)  # #D4AF37 — mid gold
_GOLD_MID     = _rgb(230, 185,  30)  # warm gold
_GOLD_DARK    = _rgb(120,  80,   0)  # dark amber — empty XP bar
_AMBER        = _rgb(255, 176,   0)  # #FFB000
_GREEN_BRIGHT = _rgb( 80, 250, 123)  # #50FA7B — healthy needs / win
_GREEN        = _rgb( 40, 167,  69)
_YELLOW       = _rgb(241, 250, 140)  # #F1FA8C — warning needs / draw
_RED          = _rgb(255,  85,  85)  # #FF5555 — critical needs / loss
_CYAN         = _rgb(139, 233, 253)  # #8BE9FD
_PURPLE       = _rgb(189, 147, 249)  # #BD93F9
_ORANGE       = _rgb(255, 166,   0)  # #FFA600
_SILVER       = _rgb(192, 192, 210)
_WHITE        = _rgb(248, 248, 242)  # #F8F8F2

# ── Sound helpers ─────────────────────────────────────────────────────────────

def _play_system_sound(name: str) -> None:
    """Play a macOS system sound, non-blocking. Silent on non-mac or missing file."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.Popen(
            ["afplay", f"/System/Library/Sounds/{name}.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def play_level_up_sound() -> None:
    """Chime for level-up."""
    _play_system_sound("Glass")


def play_evolution_sound() -> None:
    """Purr for stage evolution."""
    _play_system_sound("Purr")


def play_legendary_sound() -> None:
    """Distinctive sound for legendary unlock."""
    _play_system_sound("Funk")


# ── Text effects ──────────────────────────────────────────────────────────────

def _shimmer_able(text: str = "ABLE") -> str:
    """Gold shimmer gradient across text — instant, no animation loop."""
    if not _COLORS_ON:
        return text
    sweep = [
        _rgb(150, 100,   0),
        _rgb(200, 155,  20),
        _rgb(255, 215,   0),
        _rgb(255, 240, 100),
        _rgb(255, 215,   0),
        _rgb(200, 155,  20),
        _rgb(150, 100,   0),
    ]
    n = len(sweep)
    out = _BOLD
    for i, ch in enumerate(text):
        out += f"{sweep[i % n]}{ch}"
    out += _RESET
    return out


def _species_art_color(species: str) -> str:
    """Primary art color for each species element."""
    return {
        "blaze":   _ORANGE,
        "wave":    _CYAN,
        "root":    _GREEN_BRIGHT,
        "spark":   _YELLOW,
        "phantom": _PURPLE,
        "aether":  _SILVER,
    }.get(species.lower(), _WHITE)


def _color_art(art_lines: list[str], species: str) -> list[str]:
    """Apply species color to ASCII art lines, preserving blank lines."""
    if not _COLORS_ON:
        return list(art_lines)
    color = _species_art_color(species)
    return [f"{color}{line}{_RESET}" if line.strip() else line for line in art_lines]


# ── Bar renderers ─────────────────────────────────────────────────────────────

def _progress_bar(pct: float, width: int = 12) -> str:
    filled = int(pct / 100 * width)
    empty  = width - filled
    if _COLORS_ON:
        filled_str = f"{_BOLD}{_GOLD_BRIGHT}{'█' * filled}{_RESET}"
        empty_str  = f"{_GOLD_DARK}{'░' * empty}{_RESET}"
        return f"[{filled_str}{empty_str}]"
    return f"[{'█' * filled}{'░' * empty}]"


def _need_bar(value: float, width: int = 8) -> str:
    filled = int(value / 100 * width)
    empty  = width - filled
    if value >= 70:
        color, indicator = _GREEN_BRIGHT, "█"
    elif value >= 30:
        color, indicator = _YELLOW, "▓"
    else:
        color, indicator = _RED, "▒"
    if _COLORS_ON:
        return f"{color}{indicator * filled}{_DIM}{'░' * empty}{_RESET}"
    return f"{indicator * filled}{'░' * empty}"


# ── Profile label helper ──────────────────────────────────────────────────────

def _profile_label(value: str) -> str:
    labels = {
        "solo-operator":  "Solo operator",
        "builder":        "Builder",
        "client-delivery": "Client delivery",
        "mixed-team":     "Mixed team",
        "all-terrain":    "All-terrain",
        "coding":         "Coding",
        "research":       "Research",
        "operations":     "Operations",
        "creative":       "Creative",
        "security":       "Security",
        "general-business": "General business",
        "9b-fast-local":  "9B fast local",
        "27b-deep-h100":  "27B deep H100",
        "hybrid":         "Hybrid",
    }
    return labels.get(value, value.replace("-", " "))


# ── Public render functions ───────────────────────────────────────────────────

def render_banner(buddy: BuddyState) -> str:
    """Compact one-line buddy banner for the chat startup."""
    meta = buddy.meta
    emoji = buddy.display_emoji
    label = meta["label"]
    stage_name = STAGE_NAMES[buddy.stage_enum]
    bar = _progress_bar(buddy.xp_progress_pct, 10)
    needs = buddy.get_needs()
    mood_icon = {
        "thriving":  "\u2728",
        "content":   "\u2714",
        "hungry":    "\u26a0",
        "neglected": "\u2757",
    }.get(needs.mood, "\u2022")
    rarity = f" [{buddy.rarity_label}]" if buddy.rarity_label != "Standard" else ""
    return (
        f"  {emoji} {buddy.name} the {label}{rarity}  "
        f"Lv.{buddy.level}  {bar}  "
        f"Stage: {stage_name}  "
        f"Wins:{buddy.battles_won} Draws:{buddy.battles_drawn} Losses:{buddy.battles_lost}  "
        f"{mood_icon} {needs.mood.title()}"
    )


def render_header(buddy: BuddyState, provider_count: int) -> str:
    """Claude Code-style startup header — colored ASCII art left, stats right.

    Designed to be the first thing the user sees when running ``able chat``.
    Uses ANSI truecolor for gold shimmer title, species-tinted art, and a
    gold XP bar — zero external dependencies.
    """
    meta    = buddy.meta
    art_key = f"art_stage{buddy.stage}"
    # Raw lines used for width calculation
    raw_art_lines: list[str] = list(meta.get(art_key, meta["art_stage1"]))
    # Colored lines rendered to terminal
    art_lines = _color_art(raw_art_lines, buddy.species)

    needs = buddy.get_needs()
    mood_icon = {
        "thriving":  "\u2728",
        "content":   "\u2714\ufe0f",
        "hungry":    "\u26a0\ufe0f",
        "neglected": "\u2757",
    }.get(needs.mood, "\u2022")
    stage_name = STAGE_NAMES[buddy.stage_enum]
    xp_bar     = _progress_bar(buddy.xp_progress_pct, 10)
    rarity     = f" \u00b7 {buddy.rarity_label}" if buddy.rarity_label != "Standard" else ""

    title = _shimmer_able("ABLE")
    name_colored = (
        _c(_species_art_color(buddy.species) + _BOLD, buddy.name)
        if _COLORS_ON else buddy.name
    )
    if provider_count > 0:
        providers_label = (
            _c(_DIM, f"{provider_count} AI providers ready")
            if _COLORS_ON else f"{provider_count} AI providers ready"
        )
    else:
        providers_label = _c(_DIM, "connecting…") if _COLORS_ON else "connecting…"

    info = [
        title,
        f"{buddy.display_emoji} {name_colored} the {meta['label']}  Lv.{buddy.level}  {xp_bar}{rarity}",
        f"{stage_name} \u00b7 {mood_icon} {needs.mood.title()} \u00b7 {providers_label}",
        f"\u2764\ufe0f{needs.hunger:.0f}  \U0001f4a7{needs.thirst:.0f}  \u26a1{needs.energy:.0f}"
        f"  \u00b7  Wins {buddy.battles_won}  Draws {buddy.battles_drawn}  Losses {buddy.battles_lost}",
    ]

    # Use raw (non-ANSI) width for column alignment
    art_width = max((len(line) for line in raw_art_lines), default=0)
    while len(art_lines) < len(info):
        art_lines.append("")
        raw_art_lines.append("")
    while len(info) < len(art_lines):
        info.append("")

    gap   = "        "
    lines = []
    for colored_line, raw_line, info_line in zip(art_lines, raw_art_lines, info):
        padding = " " * max(0, art_width - len(raw_line))
        lines.append(f"    {colored_line}{padding}{gap}{info_line}")

    if buddy.catch_phrase:
        lines.append(f"    {' ' * art_width}{gap}\"{buddy.catch_phrase}\"")

    return "\n".join(lines)


def render_full(buddy: BuddyState, stats: BuddyStats | None = None) -> str:
    """Full buddy display for /buddy command."""
    meta       = buddy.meta
    emoji      = buddy.display_emoji
    stage_name = STAGE_NAMES[buddy.stage_enum]
    art_key    = f"art_stage{buddy.stage}"
    art_lines  = _color_art(list(meta.get(art_key, meta["art_stage1"])), buddy.species)

    border = _c(_GOLD, "=" * 42) if _COLORS_ON else "=" * 42
    sub    = _c(_DIM,  "─" * 42) if _COLORS_ON else "─" * 42
    lines  = []
    lines.append(border)
    lines.append(f"  {emoji}  {buddy.name} the {meta['label']}  —  \"{buddy.catch_phrase}\"")
    lines.append(sub)

    for art_line in art_lines:
        lines.append(f"         {art_line}")

    lines.append(sub)
    lines.append(f"  Type: {meta['element']}  ·  Role: {meta['role']}")
    lines.append(f"  Best for: {meta['best_for']}")
    lines.append(f"  Abilities: {', '.join(meta['abilities'])}")
    lines.append(sub)
    lines.append(
        f"  Level {buddy.level}  "
        f"{_progress_bar(buddy.xp_progress_pct, 14)}  "
        f"{buddy.xp} XP  ({buddy.xp_to_next} to next)"
    )
    lines.append(f"  Stage: {stage_name}  ({buddy.stage_enum.value}/3)")
    lines.append(f"  Rarity: {buddy.rarity_label}")
    if buddy.is_legendary:
        lines.append(f"  Legendary form: {buddy.legendary_title}")

    next_stage = Stage(min(buddy.stage + 1, 3))
    if next_stage != buddy.stage_enum:
        reqs = EVOLUTION_REQUIREMENTS[next_stage]
        lines.append(f"  Next evolution: {reqs['description']}")
    elif not buddy.is_legendary:
        lines.append("  Legendary path: earned from sustained system performance.")
        lines.append(f"  Need: {buddy.best_battle_streak}/3 streak | {LEGENDARY_REQUIREMENTS['description']}")

    lines.append(sub)
    lines.append(
        f"  Battles  W:{buddy.battles_won}  D:{buddy.battles_drawn}  L:{buddy.battles_lost}  "
        f"| Interactions: {buddy.total_interactions}"
    )
    lines.append(
        f"  Eval passes: {buddy.eval_passes}  "
        f"| Distillation pairs: {buddy.distillation_pairs}  "
        f"| Evo deploys: {buddy.evolution_deploys}"
    )
    lines.append(
        f"  Battle streak: {buddy.current_battle_streak} current  "
        f"| Best streak: {buddy.best_battle_streak}"
    )

    needs = buddy.get_needs()
    lines.append(sub)
    lines.append(f"  Needs:  ({needs.mood.title()} — {needs.mood_message})")
    lines.append(f"    Hunger:  {_need_bar(needs.hunger)}  {needs.hunger:.0f}/100  (feed: /battle)")
    lines.append(f"    Thirst:  {_need_bar(needs.thirst)}  {needs.thirst:.0f}/100  (water: /evolve)")
    lines.append(f"    Energy:  {_need_bar(needs.energy)}  {needs.energy:.0f}/100  (walk: new domains)")

    if stats:
        lines.append(sub)
        lines.append("  Live Stats (from interaction log):")
        for label, val in stats.as_dict().items():
            filled = int(val / 100 * 10)
            empty  = 10 - filled
            lines.append(f"    {label}: {'█' * filled}{'░' * empty} {val:.0f}")

    lines.append(border)
    return "\n".join(lines)


def render_evolution(buddy: BuddyState, from_stage: Stage, new_stage: Stage) -> str:
    """Dramatic evolution announcement with color and sound trigger."""
    meta      = buddy.meta
    emoji     = buddy.display_emoji
    old_name  = STAGE_NAMES[from_stage]
    new_name  = STAGE_NAMES[new_stage]
    art_key   = f"art_stage{new_stage.value}"
    art_lines = _color_art(list(meta.get(art_key, [])), buddy.species)

    play_evolution_sound()

    if _COLORS_ON:
        star_line  = _c(_GOLD_BRIGHT + _BOLD, "  " + "★ " * 21)
        head_line  = f"  ★  {emoji} {_c(_BOLD + _GOLD_BRIGHT, buddy.name + ' IS EVOLVING!')}  {emoji}"
        stage_line = f"  ★  {_c(_GOLD, old_name)}  →  {_c(_GREEN_BRIGHT + _BOLD, new_name)}"
        reached    = _c(_GOLD_BRIGHT, f"  {buddy.name} reached Stage {new_stage.value}!")
    else:
        star_line  = "  " + "*" * 42
        head_line  = f"  *  {emoji} {buddy.name} IS EVOLVING!  {emoji}"
        stage_line = f"  *  {old_name}  -->  {new_name}"
        reached    = f"  {buddy.name} reached Stage {new_stage.value}!"

    lines = ["", star_line, head_line, stage_line, star_line, ""]
    for art_line in art_lines:
        lines.append(f"           {art_line}")
    lines.extend(["", reached, ""])
    return "\n".join(lines)


def render_legendary_unlock(buddy: BuddyState) -> str:
    """Announcement when the buddy reaches its legendary form."""
    play_legendary_sound()

    crown = "\U0001f451"
    if _COLORS_ON:
        border     = _c(_GOLD_BRIGHT + _BOLD, "  " + "═" * 42)
        title_line = f"  {crown} {_c(_BOLD + _GOLD_BRIGHT, buddy.name + ' awakened its legendary form!')}"
        tag_title  = f"  {_c(_GOLD, 'Title:')} {buddy.legendary_title}"
        tag_rarity = f"  {_c(_AMBER, 'Rarity:')} {buddy.rarity_label}"
    else:
        border     = "  " + "=" * 42
        title_line = f"  {crown} {buddy.name} awakened its legendary form!"
        tag_title  = f"  Title: {buddy.legendary_title}"
        tag_rarity = f"  Rarity: {buddy.rarity_label}"

    return "\n".join(["", border, title_line, tag_title, tag_rarity, border, ""])


def render_battle_result(
    buddy: BuddyState,
    domain: str,
    passed: int,
    total: int,
    result: str,
    xp_earned: int,
) -> str:
    """Battle outcome display."""
    pct   = (passed / total * 100) if total > 0 else 0
    emoji = buddy.meta["emoji"]

    result_cfg = {
        "win":  (_GREEN_BRIGHT + _BOLD, f"  {emoji} VICTORY! {emoji}"),
        "draw": (_YELLOW,               f"  {emoji} DRAW {emoji}"),
        "loss": (_RED,                  f"  {emoji} DEFEAT {emoji}"),
    }

    border = _c(_DIM, "─" * 36) if _COLORS_ON else "─" * 36
    domain_line = f"  BATTLE: {_c(_BOLD, domain.upper()) if _COLORS_ON else domain.upper()}"
    score_color = _GREEN_BRIGHT if pct >= 70 else (_YELLOW if pct >= 40 else _RED)
    score_line  = f"  Score: {_c(score_color, f'{passed}/{total} ({pct:.0f}%)') if _COLORS_ON else f'{passed}/{total} ({pct:.0f}%)'}"

    code, text = result_cfg.get(result, (_WHITE, f"  {result}"))
    result_line = _c(code, text) if _COLORS_ON else text
    xp_line     = (_c(_GOLD_BRIGHT + _BOLD, f"  +{xp_earned} XP") if _COLORS_ON
                   else f"  +{xp_earned} XP")

    return "\n".join([border, domain_line, border, score_line, result_line, xp_line, border])


def render_backpack(collection: BuddyCollection | None) -> str:
    """Backpack / dex view for owned buddies, progress, and completion rewards."""
    if collection is None or not collection.buddies:
        return "  No buddies caught yet."

    border = _c(_GOLD, "=" * 54) if _COLORS_ON else "=" * 54
    sub    = _c(_DIM,  "─" * 54) if _COLORS_ON else "─" * 54
    lines  = [border, "  Buddy Backpack", border]

    owned        = collection.list_buddies()
    starter_ids  = {species.value for species in STARTER_SPECIES}
    starter_owned = [b for b in owned if b.species in starter_ids]
    hidden_buddy  = next((b for b in owned if b.species == HIDDEN_SIGNAL_SPECIES.value), None)
    lines.append(f"  Caught: {len(starter_owned)}/{len(STARTER_SPECIES)} starters")

    if collection.operator_profile:
        focus      = collection.operator_profile.get("focus", "unset")
        work_style = collection.operator_profile.get("work_style", "unset")
        distill    = collection.operator_profile.get("distillation_track", "unset")
        lines.append("  Operator profile")
        lines.append(
            f"    Focus: {_profile_label(focus)}  ·  "
            f"Style: {_profile_label(work_style)}  ·  "
            f"Distill: {_profile_label(distill)}"
        )

    for buddy in starter_owned:
        active     = "▶" if collection.active_species == buddy.species else " "
        stage      = STAGE_NAMES[buddy.stage_enum]
        name_col   = _c(_species_art_color(buddy.species), buddy.name) if _COLORS_ON else buddy.name
        lines.append(
            f"  {active} {buddy.display_emoji} {name_col:<12} "
            f"Lv.{buddy.level:<3} {stage:<8} {buddy.rarity_label:<16} {buddy.meta['role']}"
        )

    missing = [s for s in STARTER_SPECIES if s.value not in collection.buddies]
    if missing:
        lines.append(sub)
        lines.append("  Uncaught")
        for species in missing:
            meta     = SPECIES_META[species]
            progress = min(collection.get_progress(species), CATCH_PROGRESS_TARGET)
            lines.append(
                f"  [{progress:>2}/{CATCH_PROGRESS_TARGET}] {meta['emoji']} {meta['label']} "
                f"· {meta['best_for']}"
            )

    if hidden_buddy:
        lines.append(sub)
        secret_stage = STAGE_NAMES[hidden_buddy.stage_enum]
        lines.append("  Collection Bonus")
        lines.append(
            f"  {'▶' if collection.active_species == hidden_buddy.species else ' '} "
            f"{hidden_buddy.display_emoji} {hidden_buddy.name} "
            f"Lv.{hidden_buddy.level} {secret_stage} {hidden_buddy.rarity_label}"
        )
        if hidden_buddy.level < SECRET_SIGNAL_LEVEL or not hidden_buddy.is_legendary:
            lines.append(
                f"  Final mastery path: Stage 3 + legendary + level {SECRET_SIGNAL_LEVEL}"
            )

    if collection.badges:
        lines.append(sub)
        lines.append("  Badges")
        for badge in collection.badges:
            lines.append(f"  🏅 {badge['title']} — {badge['description']}")

    lines.append(border)
    return "\n".join(lines)


def render_starter_selection() -> str:
    """Starter selection menu for first run."""
    border = _c(_GOLD + _BOLD, "=" * 72) if _COLORS_ON else "=" * 72
    lines  = ["", border, "  Choose your ABLE buddy"]
    lines.append("  This affects buddy theme + bonus XP only. It does not change routing or tools.")
    lines.append("  If you do mixed work, any starter is fine. Root is the steadiest general operator pick.")
    lines.append(border)
    lines.append("")

    for i, species in enumerate(STARTER_SPECIES, 1):
        meta       = SPECIES_META[species]
        emoji      = meta["emoji"]
        label      = meta["label"]
        desc       = meta["desc"]
        bonus      = ", ".join(meta["bonus_domains"][:3])
        abilities  = ", ".join(meta["abilities"][:3])
        art        = _color_art(list(meta["art_stage1"]), species.value)

        num = _c(_GOLD_BRIGHT + _BOLD, f"[{i}]") if _COLORS_ON else f"[{i}]"
        lbl = _c(_species_art_color(species.value) + _BOLD, label) if _COLORS_ON else label
        lines.append(f"  {num} {emoji} {lbl}  ·  {meta['element']}  ·  {meta['role']}")
        lines.append(f"      {desc}")
        lines.append(f"      Best for: {meta['best_for']}")
        lines.append(f"      Bonus XP: {bonus}")
        lines.append(f"      Abilities: {abilities}")
        for art_line in art:
            lines.append(f"         {art_line}")
        lines.append("")

    lines.append(border)
    lines.append("  Rare hatch chance: some starters emerge as Shiny variants.")
    lines.append("  Interactive chat requires a starter pick. Non-interactive sessions skip this flow.")
    return "\n".join(lines)
