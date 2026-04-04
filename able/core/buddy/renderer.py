"""
Buddy CLI renderer — ASCII art display for the terminal.

Shows the buddy's current state, stats, and evolution stage in `able chat`.

Color notes
-----------
Uses standard 16-color ANSI codes only — not 24-bit truecolor — so output
renders correctly on every ANSI-capable terminal (macOS Terminal, iTerm2,
ssh sessions, Windows Terminal, VS Code integrated terminal, etc.).

Call ``force_colors(True/False)`` from the host module (chat.py) to
explicitly set the color mode rather than relying on runtime isatty() checks.
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

# The host module (chat.py) calls force_colors() after its own reliable
# isatty() check.  This sidesteps import-order / asyncio timing issues.
_COLORS_OVERRIDE: "bool | None" = None


def force_colors(enabled: bool) -> None:
    """Explicitly set color output.  Call from chat.py with its own _COLOR flag."""
    global _COLORS_OVERRIDE
    _COLORS_OVERRIDE = enabled


class _ColorFlag:
    """Lazy color flag: respects force_colors() override, then falls back to isatty()."""

    def __bool__(self) -> bool:
        if _COLORS_OVERRIDE is not None:
            return bool(_COLORS_OVERRIDE)
        return (
            not os.environ.get("NO_COLOR")
            and hasattr(sys.stdout, "isatty")
            and bool(sys.stdout.isatty())
        )

    def __repr__(self) -> str:
        return f"_ColorFlag(active={bool(self)})"


_COLORS_ON = _ColorFlag()

# ── Standard 16-color ANSI palette ───────────────────────────────────────────
# Works on ALL ANSI terminals — no 24-bit truecolor required.

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_GOLD   = "\033[1;33m"   # bold yellow → gold/amber on dark backgrounds
_YELLOW = "\033[33m"     # standard yellow
_GREEN  = "\033[92m"     # bright green   (healthy / victory)
_RED    = "\033[91m"     # bright red     (critical / defeat)
_CYAN   = "\033[96m"     # bright cyan    (water element)
_PURPLE = "\033[95m"     # bright magenta (shadow element)
_WHITE  = "\033[97m"     # bright white   (aether element)
_ORANGE = "\033[33m"     # closest 16-color to orange (fire element)

_ANSI_ESCAPE = re.compile(r"\033\[[^m]*m")


def _c(code: str, text: str) -> str:
    """Apply ANSI code + reset if colors are enabled."""
    return f"{code}{text}{_RESET}" if _COLORS_ON else text


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


# ── Species palette ───────────────────────────────────────────────────────────

def _species_art_color(species: str) -> str:
    return {
        "blaze":   "\033[91m",   # bright red/orange
        "wave":    "\033[96m",   # bright cyan
        "root":    "\033[92m",   # bright green
        "spark":   "\033[93m",   # bright yellow
        "phantom": "\033[95m",   # bright magenta
        "aether":  "\033[97m",   # bright white
    }.get(species.lower(), "\033[97m")


def _color_art(art_lines: list[str], species: str) -> list[str]:
    if not _COLORS_ON:
        return list(art_lines)
    color = _species_art_color(species)
    return [f"{color}{line}{_RESET}" if line.strip() else line for line in art_lines]


# ── Sound helpers ─────────────────────────────────────────────────────────────

def _play_system_sound(name: str) -> None:
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
    _play_system_sound("Glass")


def play_evolution_sound() -> None:
    _play_system_sound("Purr")


def play_legendary_sound() -> None:
    _play_system_sound("Funk")


# ── Bar renderers ─────────────────────────────────────────────────────────────

def _progress_bar(pct: float, width: int = 12) -> str:
    filled = int(pct / 100 * width)
    empty  = width - filled
    if _COLORS_ON:
        return f"[{_GOLD}{'█' * filled}{_RESET}{_DIM}{'░' * empty}{_RESET}]"
    return f"[{'█' * filled}{'░' * empty}]"


def _need_bar(value: float, width: int = 8) -> str:
    filled = int(value / 100 * width)
    empty  = width - filled
    if value >= 70:
        color, ch = _GREEN,  "█"
    elif value >= 30:
        color, ch = _YELLOW, "▓"
    else:
        color, ch = _RED,    "▒"
    if _COLORS_ON:
        return f"{color}{ch * filled}{_DIM}{'░' * empty}{_RESET}"
    return f"{ch * filled}{'░' * empty}"


# ── Profile label helper ──────────────────────────────────────────────────────

def _profile_label(value: str) -> str:
    return {
        "solo-operator":    "Solo operator",
        "builder":          "Builder",
        "client-delivery":  "Client delivery",
        "mixed-team":       "Mixed team",
        "all-terrain":      "All-terrain",
        "coding":           "Coding",
        "research":         "Research",
        "operations":       "Operations",
        "creative":         "Creative",
        "security":         "Security",
        "general-business": "General business",
        "9b-fast-local":    "9B fast local",
        "27b-deep-h100":    "27B deep H100",
        "hybrid":           "Hybrid",
    }.get(value, value.replace("-", " "))


# ── Public render functions ───────────────────────────────────────────────────

def render_banner(buddy: BuddyState) -> str:
    """Compact one-line buddy banner."""
    meta       = buddy.meta
    stage_name = STAGE_NAMES[buddy.stage_enum]
    bar        = _progress_bar(buddy.xp_progress_pct, 10)
    needs      = buddy.get_needs()
    mood_icon  = {"thriving": "✨", "content": "✔", "hungry": "⚠", "neglected": "❗"}.get(needs.mood, "•")
    rarity     = f" [{buddy.rarity_label}]" if buddy.rarity_label != "Standard" else ""
    return (
        f"  {buddy.display_emoji} {buddy.name} the {meta['label']}{rarity}  "
        f"Lv.{buddy.level}  {bar}  Stage: {stage_name}  "
        f"Wins:{buddy.battles_won} Draws:{buddy.battles_drawn} Losses:{buddy.battles_lost}  "
        f"{mood_icon} {needs.mood.title()}"
    )


def render_header(buddy: BuddyState, provider_count: int) -> str:
    """Startup header — colored ASCII art left, buddy stats right.

    Uses standard 16-color ANSI for maximum terminal compatibility.
    provider_count=0 → shows 'connecting…' while gateway initialises.
    """
    meta    = buddy.meta
    art_key = f"art_stage{buddy.stage}"

    # Raw lines for width measurement; colored lines for display
    raw_art: list[str] = list(meta.get(art_key, meta["art_stage1"]))
    col_art: list[str] = _color_art(raw_art, buddy.species)

    needs      = buddy.get_needs()
    mood_icon  = {"thriving": "✨", "content": "✔️", "hungry": "⚠️", "neglected": "❗"}.get(needs.mood, "•")
    stage_name = STAGE_NAMES[buddy.stage_enum]
    xp_bar     = _progress_bar(buddy.xp_progress_pct, 10)
    rarity     = f" · {buddy.rarity_label}" if buddy.rarity_label != "Standard" else ""

    # Title
    title = _c(_GOLD, "ABLE") if _COLORS_ON else "ABLE"

    # Name colored by species
    name_str = _c(_species_art_color(buddy.species) + _BOLD, buddy.name) if _COLORS_ON else buddy.name

    # Provider count
    if provider_count > 0:
        prov_str = _c(_DIM, f"{provider_count} AI providers ready") if _COLORS_ON else f"{provider_count} AI providers ready"
    else:
        prov_str = _c(_DIM, "connecting…") if _COLORS_ON else "connecting…"

    info = [
        title,
        f"{buddy.display_emoji} {name_str} the {meta['label']}  Lv.{buddy.level}  {xp_bar}{rarity}",
        f"{stage_name} · {mood_icon} {needs.mood.title()} · {prov_str}",
        f"❤️{needs.hunger:.0f}  💧{needs.thirst:.0f}  ⚡{needs.energy:.0f}"
        f"  ·  Wins {buddy.battles_won}  Draws {buddy.battles_drawn}  Losses {buddy.battles_lost}",
    ]

    # Pad both lists to same length
    art_width = max((len(ln) for ln in raw_art), default=0)
    while len(col_art) < len(info):
        col_art.append("")
        raw_art.append("")
    while len(info) < len(col_art):
        info.append("")

    gap   = "      "   # 6 spaces between art and info columns
    indent = "  "      # 2-space left margin
    lines: list[str] = []
    for col_line, raw_line, info_line in zip(col_art, raw_art, info):
        padding = " " * max(0, art_width - len(raw_line))
        lines.append(f"{indent}{col_line}{padding}{gap}{info_line}")

    if buddy.catch_phrase:
        lines.append(f"{indent}{' ' * art_width}{gap}\"{buddy.catch_phrase}\"")

    return "\n".join(lines)


def render_full(buddy: BuddyState, stats: BuddyStats | None = None) -> str:
    """Full buddy display for /buddy command."""
    meta       = buddy.meta
    stage_name = STAGE_NAMES[buddy.stage_enum]
    art_key    = f"art_stage{buddy.stage}"
    art_lines  = _color_art(list(meta.get(art_key, meta["art_stage1"])), buddy.species)

    border = _c(_GOLD, "=" * 42) if _COLORS_ON else "=" * 42
    sub    = _c(_DIM,  "─" * 42) if _COLORS_ON else "─" * 42
    lines  = [border]
    lines.append(f"  {buddy.display_emoji}  {buddy.name} the {meta['label']}  —  \"{buddy.catch_phrase}\"")
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
        f"  Battles  W:{buddy.battles_won}  D:{buddy.battles_drawn}  L:{buddy.battles_lost}"
        f"  | Interactions: {buddy.total_interactions}"
    )
    lines.append(
        f"  Eval passes: {buddy.eval_passes}  "
        f"| Distillation pairs: {buddy.distillation_pairs}  "
        f"| Evo deploys: {buddy.evolution_deploys}"
    )
    lines.append(
        f"  Battle streak: {buddy.current_battle_streak} current"
        f"  | Best streak: {buddy.best_battle_streak}"
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
            lines.append(f"    {label}: {'█' * filled}{'░' * (10 - filled)} {val:.0f}")

    lines.append(border)
    return "\n".join(lines)


def render_evolution(buddy: BuddyState, from_stage: Stage, new_stage: Stage) -> str:
    """Dramatic evolution announcement."""
    meta      = buddy.meta
    emoji     = buddy.display_emoji
    old_name  = STAGE_NAMES[from_stage]
    new_name  = STAGE_NAMES[new_stage]
    art_key   = f"art_stage{new_stage.value}"
    art_lines = _color_art(list(meta.get(art_key, [])), buddy.species)

    play_evolution_sound()

    if _COLORS_ON:
        border     = _c(_GOLD, "  " + "★ " * 20 + "★")
        title_line = f"  ★  {emoji} {_c(_GOLD, buddy.name + ' IS EVOLVING!')}  {emoji}"
        stage_line = f"  ★  {old_name}  →  {_c(_GREEN + _BOLD, new_name)}"
        reached    = _c(_GOLD, f"  {buddy.name} reached Stage {new_stage.value}!")
    else:
        border     = "  " + "*" * 42
        title_line = f"  *  {emoji} {buddy.name} IS EVOLVING!  {emoji}"
        stage_line = f"  *  {old_name}  -->  {new_name}"
        reached    = f"  {buddy.name} reached Stage {new_stage.value}!"

    lines = ["", border, title_line, stage_line, border, ""]
    for art_line in art_lines:
        lines.append(f"           {art_line}")
    lines.extend(["", reached, ""])
    return "\n".join(lines)


def render_legendary_unlock(buddy: BuddyState) -> str:
    """Legendary form announcement."""
    play_legendary_sound()

    crown = "👑"
    if _COLORS_ON:
        border     = _c(_GOLD, "  " + "═" * 42)
        title_line = f"  {crown} {_c(_GOLD + _BOLD, buddy.name + ' awakened its legendary form!')}"
        tag_title  = f"  {_c(_YELLOW, 'Title:')} {buddy.legendary_title}"
        tag_rarity = f"  {_c(_DIM, 'Rarity:')} {buddy.rarity_label}"
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
        "win":  (_GREEN + _BOLD, f"  {emoji} VICTORY! {emoji}"),
        "draw": (_YELLOW,        f"  {emoji} DRAW {emoji}"),
        "loss": (_RED,           f"  {emoji} DEFEAT {emoji}"),
    }
    code, text = result_cfg.get(result, (_WHITE, f"  {result}"))

    border      = _c(_DIM, "─" * 36) if _COLORS_ON else "─" * 36
    domain_line = f"  BATTLE: {_c(_BOLD, domain.upper()) if _COLORS_ON else domain.upper()}"
    score_color = _GREEN if pct >= 70 else (_YELLOW if pct >= 40 else _RED)
    score_line  = f"  Score: {_c(score_color, f'{passed}/{total} ({pct:.0f}%)') if _COLORS_ON else f'{passed}/{total} ({pct:.0f}%)'}"
    result_line = _c(code, text) if _COLORS_ON else text
    xp_line     = _c(_GOLD + _BOLD, f"  +{xp_earned} XP") if _COLORS_ON else f"  +{xp_earned} XP"

    return "\n".join([border, domain_line, border, score_line, result_line, xp_line, border])


def render_backpack(collection: BuddyCollection | None) -> str:
    """Backpack / dex view."""
    if collection is None or not collection.buddies:
        return "  No buddies caught yet."

    border = _c(_GOLD, "=" * 54) if _COLORS_ON else "=" * 54
    sub    = _c(_DIM,  "─" * 54) if _COLORS_ON else "─" * 54
    lines  = [border, "  Buddy Backpack", border]

    owned         = collection.list_buddies()
    starter_ids   = {s.value for s in STARTER_SPECIES}
    starter_owned = [b for b in owned if b.species in starter_ids]
    hidden_buddy  = next((b for b in owned if b.species == HIDDEN_SIGNAL_SPECIES.value), None)
    lines.append(f"  Caught: {len(starter_owned)}/{len(STARTER_SPECIES)} starters")

    if collection.operator_profile:
        focus   = collection.operator_profile.get("focus", "unset")
        style   = collection.operator_profile.get("work_style", "unset")
        distill = collection.operator_profile.get("distillation_track", "unset")
        lines.append("  Operator profile")
        lines.append(
            f"    Focus: {_profile_label(focus)}  ·  "
            f"Style: {_profile_label(style)}  ·  "
            f"Distill: {_profile_label(distill)}"
        )

    for buddy in starter_owned:
        active   = "▶" if collection.active_species == buddy.species else " "
        stage    = STAGE_NAMES[buddy.stage_enum]
        name_col = _c(_species_art_color(buddy.species), buddy.name) if _COLORS_ON else buddy.name
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
                f"  [{progress:>2}/{CATCH_PROGRESS_TARGET}] {meta['emoji']} {meta['label']} · {meta['best_for']}"
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
            lines.append(f"  Final mastery path: Stage 3 + legendary + level {SECRET_SIGNAL_LEVEL}")

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
        meta      = SPECIES_META[species]
        art       = _color_art(list(meta["art_stage1"]), species.value)
        num       = _c(_GOLD + _BOLD, f"[{i}]") if _COLORS_ON else f"[{i}]"
        lbl       = _c(_species_art_color(species.value) + _BOLD, meta["label"]) if _COLORS_ON else meta["label"]
        bonus     = ", ".join(meta["bonus_domains"][:3])
        abilities = ", ".join(meta["abilities"][:3])
        lines.append(f"  {num} {meta['emoji']} {lbl}  ·  {meta['element']}  ·  {meta['role']}")
        lines.append(f"      {meta['desc']}")
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
