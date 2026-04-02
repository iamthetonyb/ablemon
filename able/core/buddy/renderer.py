"""
Buddy CLI renderer — ASCII art display for the terminal.

Shows the buddy's current state, stats, and evolution stage in `able chat`.
"""

from __future__ import annotations

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


def _progress_bar(pct: float, width: int = 12) -> str:
    filled = int(pct / 100 * width)
    empty = width - filled
    return f"[{'\u2588' * filled}{'\u2591' * empty}]"


def _need_bar(value: float, width: int = 8) -> str:
    filled = int(value / 100 * width)
    empty = width - filled
    if value >= 70:
        indicator = "\u2588"  # Full block — healthy
    elif value >= 30:
        indicator = "\u2593"  # Dark shade — warning
    else:
        indicator = "\u2591"  # Light shade — critical
    return f"{indicator * filled}{'\u2591' * empty}"


def render_banner(buddy: BuddyState) -> str:
    """Compact one-line buddy banner for the chat startup."""
    meta = buddy.meta
    emoji = buddy.display_emoji
    label = meta["label"]
    stage_name = STAGE_NAMES[buddy.stage_enum]
    bar = _progress_bar(buddy.xp_progress_pct, 10)
    needs = buddy.get_needs()
    mood_icon = {"thriving": "\u2728", "content": "\u2714", "hungry": "\u26a0", "neglected": "\u2757"}.get(needs.mood, "\u2022")
    rarity = f" [{buddy.rarity_label}]" if buddy.rarity_label != "Standard" else ""
    return (
        f"  {emoji} {buddy.name} the {label}{rarity}  "
        f"Lv.{buddy.level}  {bar}  "
        f"Stage: {stage_name}  "
        f"W:{buddy.battles_won} D:{buddy.battles_drawn} L:{buddy.battles_lost}  "
        f"{mood_icon} {needs.mood.title()}"
    )


def render_header(buddy: BuddyState, provider_count: int) -> str:
    """Claude Code-style startup header — ASCII art left, stats right.

    Designed to be the first thing the user sees when running ``able chat``.
    Non-technical, clean layout that mirrors Claude Code's mascot header.
    """
    meta = buddy.meta
    art_key = f"art_stage{buddy.stage}"
    art_lines = list(meta.get(art_key, meta["art_stage1"]))

    needs = buddy.get_needs()
    mood_icon = {
        "thriving": "\u2728",
        "content": "\u2714\ufe0f",
        "hungry": "\u26a0\ufe0f",
        "neglected": "\u2757",
    }.get(needs.mood, "\u2022")
    stage_name = STAGE_NAMES[buddy.stage_enum]
    xp_bar = _progress_bar(buddy.xp_progress_pct, 10)
    rarity = f" \u00b7 {buddy.rarity_label}" if buddy.rarity_label != "Standard" else ""

    # Info lines placed to the right of the ASCII art
    info = [
        f"ABLE",
        f"{buddy.display_emoji} {buddy.name} the {meta['label']}  Lv.{buddy.level}  {xp_bar}{rarity}",
        f"{stage_name} \u00b7 {mood_icon} {needs.mood.title()} \u00b7 {provider_count} providers",
        f"\u2764\ufe0f {needs.hunger:.0f}  \U0001f4a7 {needs.thirst:.0f}  \u26a1 {needs.energy:.0f}  \u00b7  W{buddy.battles_won} D{buddy.battles_drawn} L{buddy.battles_lost}",
    ]

    # Pad art to consistent width
    art_width = max((len(line) for line in art_lines), default=0)
    while len(art_lines) < len(info):
        art_lines.append("")
    while len(info) < len(art_lines):
        info.append("")

    gap = "        "
    lines = []
    for art_line, info_line in zip(art_lines, info):
        lines.append(f"    {art_line:<{art_width}}{gap}{info_line}")

    # Catch phrase below
    if buddy.catch_phrase:
        lines.append(f"    {' ' * art_width}{gap}\"{buddy.catch_phrase}\"")

    return "\n".join(lines)


def render_full(buddy: BuddyState, stats: BuddyStats | None = None) -> str:
    """Full buddy display for /buddy command."""
    meta = buddy.meta
    emoji = buddy.display_emoji
    stage_name = STAGE_NAMES[buddy.stage_enum]
    art_key = f"art_stage{buddy.stage}"
    art_lines = meta.get(art_key, meta["art_stage1"])

    lines = []
    lines.append(f"{'=' * 42}")
    lines.append(f"  {emoji}  {buddy.name} the {meta['label']}  —  \"{buddy.catch_phrase}\"")
    lines.append(f"{'─' * 42}")

    # Art centered
    for art_line in art_lines:
        lines.append(f"         {art_line}")

    lines.append(f"{'─' * 42}")
    lines.append(
        f"  Level {buddy.level}  "
        f"{_progress_bar(buddy.xp_progress_pct, 14)}  "
        f"{buddy.xp} XP  ({buddy.xp_to_next} to next)"
    )
    lines.append(f"  Stage: {stage_name}  ({buddy.stage_enum.value}/3)")
    lines.append(f"  Rarity: {buddy.rarity_label}")
    if buddy.is_legendary:
        lines.append(f"  Legendary form: {buddy.legendary_title}")

    # Evolution progress
    next_stage = Stage(min(buddy.stage + 1, 3))
    if next_stage != buddy.stage_enum:
        reqs = EVOLUTION_REQUIREMENTS[next_stage]
        lines.append(f"  Next evolution: {reqs['description']}")
    elif not buddy.is_legendary:
        lines.append("  Legendary path: earned from sustained system performance.")
        lines.append(f"  Need: {buddy.best_battle_streak}/3 streak | {LEGENDARY_REQUIREMENTS['description']}")

    lines.append(f"{'─' * 42}")
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

    # Needs / Tamagotchi layer
    needs = buddy.get_needs()
    lines.append(f"{'─' * 42}")
    lines.append(f"  Needs:  ({needs.mood.title()} — {needs.mood_message})")
    lines.append(f"    Hunger:  {_need_bar(needs.hunger)}  {needs.hunger:.0f}/100  (feed: /battle)")
    lines.append(f"    Thirst:  {_need_bar(needs.thirst)}  {needs.thirst:.0f}/100  (water: /evolve)")
    lines.append(f"    Energy:  {_need_bar(needs.energy)}  {needs.energy:.0f}/100  (walk: new domains)")

    if stats:
        lines.append(f"{'─' * 42}")
        lines.append("  Live Stats (from interaction log):")
        for label, val in stats.as_dict().items():
            filled = int(val / 100 * 10)
            empty = 10 - filled
            lines.append(
                f"    {label}: {'\u2588' * filled}{'\u2591' * empty} {val:.0f}"
            )

    lines.append(f"{'=' * 42}")
    return "\n".join(lines)


def render_evolution(buddy: BuddyState, from_stage: Stage, new_stage: Stage) -> str:
    """Dramatic evolution announcement."""
    meta = buddy.meta
    emoji = buddy.display_emoji
    old_name = STAGE_NAMES[from_stage]
    new_name = STAGE_NAMES[new_stage]
    art_key = f"art_stage{new_stage.value}"
    art_lines = meta.get(art_key, [])

    lines = []
    lines.append("")
    lines.append(f"  {'*' * 42}")
    lines.append(f"  *  {emoji} {buddy.name} IS EVOLVING!  {emoji}")
    lines.append(f"  *  {old_name}  -->  {new_name}")
    lines.append(f"  {'*' * 42}")
    lines.append("")
    for art_line in art_lines:
        lines.append(f"           {art_line}")
    lines.append("")
    lines.append(f"  {buddy.name} reached Stage {new_stage.value}!")
    lines.append("")
    return "\n".join(lines)


def render_legendary_unlock(buddy: BuddyState) -> str:
    """Announcement when the buddy reaches its legendary form."""
    lines = []
    lines.append("")
    lines.append(f"  {'=' * 42}")
    lines.append(f"  \U0001f451 {buddy.name} awakened its legendary form!")
    lines.append(f"  Title: {buddy.legendary_title}")
    lines.append(f"  Rarity: {buddy.rarity_label}")
    lines.append(f"  {'=' * 42}")
    lines.append("")
    return "\n".join(lines)


def render_battle_result(
    buddy: BuddyState,
    domain: str,
    passed: int,
    total: int,
    result: str,
    xp_earned: int,
) -> str:
    """Battle outcome display."""
    pct = (passed / total * 100) if total > 0 else 0
    emoji = buddy.meta["emoji"]

    result_art = {
        "win": f"  {emoji} VICTORY! {emoji}",
        "draw": f"  {emoji} DRAW {emoji}",
        "loss": f"  {emoji} DEFEAT {emoji}",
    }

    lines = []
    lines.append(f"{'─' * 36}")
    lines.append(f"  BATTLE: {domain.upper()}")
    lines.append(f"{'─' * 36}")
    lines.append(f"  Score: {passed}/{total} ({pct:.0f}%)")
    lines.append(result_art.get(result, f"  {result}"))
    lines.append(f"  +{xp_earned} XP")
    lines.append(f"{'─' * 36}")
    return "\n".join(lines)


def render_backpack(collection: BuddyCollection | None) -> str:
    """Backpack / dex view for owned buddies, progress, and completion rewards."""
    if collection is None or not collection.buddies:
        return "  No buddies caught yet."

    lines = []
    lines.append(f"{'=' * 54}")
    lines.append("  Buddy Backpack")
    lines.append(f"{'=' * 54}")

    owned = collection.list_buddies()
    starter_ids = {species.value for species in STARTER_SPECIES}
    starter_owned = [buddy for buddy in owned if buddy.species in starter_ids]
    hidden_buddy = next((buddy for buddy in owned if buddy.species == HIDDEN_SIGNAL_SPECIES.value), None)
    lines.append(f"  Caught: {len(starter_owned)}/{len(STARTER_SPECIES)} starters")
    if collection.operator_profile:
        focus = collection.operator_profile.get("focus", "unset")
        work_style = collection.operator_profile.get("work_style", "unset")
        distillation = collection.operator_profile.get("distillation_track", "unset")
        lines.append(
            f"  Operator profile: {focus} · {work_style} · {distillation}"
        )
    for buddy in starter_owned:
        active = "▶" if collection.active_species == buddy.species else " "
        stage = STAGE_NAMES[buddy.stage_enum]
        lines.append(
            f"  {active} {buddy.display_emoji} {buddy.name:<12} "
            f"Lv.{buddy.level:<3} {stage:<8} {buddy.rarity_label}"
        )

    missing = [species for species in STARTER_SPECIES if species.value not in collection.buddies]
    if missing:
        lines.append(f"{'─' * 54}")
        lines.append("  Uncaught")
        for species in missing:
            meta = SPECIES_META[species]
            progress = min(collection.get_progress(species), CATCH_PROGRESS_TARGET)
            lines.append(
                f"  [{progress:>2}/{CATCH_PROGRESS_TARGET}] {meta['emoji']} {meta['label']} "
                f"· {meta['best_for']}"
            )

    lines.append(f"{'─' * 54}")
    if hidden_buddy:
        secret_stage = STAGE_NAMES[hidden_buddy.stage_enum]
        lines.append("  Secret Signal")
        lines.append(
            f"  {'▶' if collection.active_species == hidden_buddy.species else ' '} "
            f"{hidden_buddy.display_emoji} {hidden_buddy.name} "
            f"Lv.{hidden_buddy.level} {secret_stage} {hidden_buddy.rarity_label}"
        )
        if hidden_buddy.level < SECRET_SIGNAL_LEVEL or not hidden_buddy.is_legendary:
            lines.append(
                f"  Final mastery path: Stage 3 + legendary + level {SECRET_SIGNAL_LEVEL}"
            )
    else:
        lines.append("  Secret Signal")
        lines.append("  ??? Locked — complete the full starter dex to awaken it.")

    if collection.badges:
        lines.append(f"{'─' * 54}")
        lines.append("  Badges")
        for badge in collection.badges:
            lines.append(f"  🏅 {badge['title']} — {badge['description']}")

    if collection.easter_egg_title:
        lines.append(f"{'─' * 54}")
        lines.append(f"  ✨ Easter Egg: {collection.easter_egg_title}")
        if collection.easter_egg_message:
            lines.append(f"  {collection.easter_egg_message}")

    lines.append(f"{'=' * 54}")
    return "\n".join(lines)


def render_starter_selection() -> str:
    """Starter selection menu for first run."""
    lines = []
    lines.append("")
    lines.append(f"{'=' * 72}")
    lines.append("  Choose your ABLE buddy")
    lines.append("  This affects buddy theme + bonus XP only. It does not change routing or tools.")
    lines.append("  If you do mixed work, any starter is fine. Root is the steadiest general operator pick.")
    lines.append(f"{'=' * 72}")
    lines.append("")

    for i, species in enumerate(STARTER_SPECIES, 1):
        meta = SPECIES_META[species]
        emoji = meta["emoji"]
        label = meta["label"]
        desc = meta["desc"]
        bonus = ", ".join(meta["bonus_domains"][:3])
        abilities = ", ".join(meta["abilities"][:3])
        art = meta["art_stage1"]

        lines.append(f"  [{i}] {emoji} {label}  ·  {meta['element']}  ·  {meta['role']}")
        lines.append(f"      {desc}")
        lines.append(f"      Best for: {meta['best_for']}")
        lines.append(f"      Bonus XP: {bonus}")
        lines.append(f"      Abilities: {abilities}")
        for art_line in art:
            lines.append(f"         {art_line}")
        lines.append("")

    lines.append(f"{'=' * 72}")
    lines.append("  Rare hatch chance: some starters emerge as Shiny variants.")
    lines.append("  Interactive chat requires a starter pick. Non-interactive sessions skip this flow.")
    return "\n".join(lines)
