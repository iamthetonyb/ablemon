"""
Buddy companion tool definitions and handlers.

Allows ABLE to report buddy status, feed/interact with the buddy,
and show the backpack — all via Telegram natural language or tool calls.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from able.core.gateway.tool_registry import ToolContext, ToolRegistry

logger = logging.getLogger(__name__)


# ── Tool Definitions ──────────────────────────────────────────────────────────

BUDDY_STATUS = {
    "type": "function",
    "function": {
        "name": "buddy_status",
        "description": (
            "Get the status of the user's buddy companion (virtual pet / mascot). "
            "Shows name, species, level, XP, stage, needs (hunger/thirst/energy), "
            "mood, battle record, and evolution progress. "
            "Use when the user asks about their buddy by name (e.g. 'how's Groot', "
            "'how is Groot doing', 'buddy status', 'check on my buddy', "
            "'what does Groot need', '/buddy'). "
            "This is NOT for tenant/client status — use tenant_status for that."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

BUDDY_FEED = {
    "type": "function",
    "function": {
        "name": "buddy_feed",
        "description": (
            "Interact with the buddy to restore its needs. "
            "Actions: 'battle' (feeds hunger via eval battle), "
            "'water' (restores thirst), 'walk' (restores energy). "
            "Use when user says 'feed Groot', 'battle', 'walk the buddy', "
            "'give Groot water', '/battle', '/evolve'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["battle", "water", "walk"],
                    "description": "Interaction type: battle (hunger), water (thirst), walk (energy)",
                },
            },
            "required": ["action"],
        },
    },
}

BUDDY_BACKPACK = {
    "type": "function",
    "function": {
        "name": "buddy_backpack",
        "description": (
            "Show the buddy backpack / collection — all caught buddies, "
            "progress toward uncaught species, badges, and operator profile. "
            "Use when user says 'backpack', 'show collection', 'buddy list', "
            "'what buddies do I have', '/backpack'."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_buddy_status(**kwargs) -> str:
    """Get detailed buddy status with ASCII art and needs."""
    try:
        from able.core.buddy.model import load_buddy, save_buddy
        from able.core.buddy.renderer import render_full

        buddy = load_buddy()
        if buddy is None:
            return (
                "No buddy exists yet. Start an interactive `able chat` session "
                "to choose your starter buddy."
            )

        # Apply needs decay so status reflects current state
        buddy.apply_needs_decay()
        save_buddy(buddy)

        # Use the full renderer (shows art, stats, needs, evolution progress)
        text = render_full(buddy)

        # Convert to Telegram-safe format (monospace block for ASCII art)
        return f"```\n{text}\n```"

    except Exception as e:
        logger.error("buddy_status failed: %s", e, exc_info=True)
        return f"Buddy status error: {e}"


async def handle_buddy_feed(action: str = "water", **kwargs) -> str:
    """Interact with buddy to restore needs."""
    try:
        from able.core.buddy.model import load_buddy, save_buddy

        buddy = load_buddy()
        if buddy is None:
            return "No buddy exists yet."

        old_level = buddy.level

        if action == "battle":
            # Quick battle — run a mini eval
            try:
                from able.core.buddy.xp import award_interaction_xp
                xp = award_interaction_xp(
                    complexity_score=0.5,
                    used_tools=True,
                    domain="coding",
                )
                buddy = load_buddy()  # Reload after XP award
                needs = buddy.get_needs()
                return (
                    f"{buddy.display_emoji} **{buddy.name}** battled!\n"
                    f"+{xp} XP | Level {buddy.level}\n"
                    f"Hunger: {needs.hunger:.0f}/100 | "
                    f"Mood: {needs.mood.title()}"
                )
            except Exception as e:
                return f"Battle failed: {e}"

        elif action == "water":
            buddy.water("telegram_water")
            buddy.award_xp(5)
            save_buddy(buddy)
            needs = buddy.get_needs()
            return (
                f"💧 **{buddy.name}** drank water!\n"
                f"Thirst: {needs.thirst:.0f}/100 | "
                f"Mood: {needs.mood.title()}"
            )

        elif action == "walk":
            buddy.walk("telegram_walk")
            buddy.award_xp(5)
            save_buddy(buddy)
            needs = buddy.get_needs()
            return (
                f"🚶 **{buddy.name}** went for a walk!\n"
                f"Energy: {needs.energy:.0f}/100 | "
                f"Mood: {needs.mood.title()}"
            )

        else:
            return f"Unknown action: {action}. Use: battle, water, walk"

    except Exception as e:
        logger.error("buddy_feed failed: %s", e, exc_info=True)
        return f"Buddy feed error: {e}"


async def handle_buddy_backpack(**kwargs) -> str:
    """Show the buddy collection / backpack."""
    try:
        from able.core.buddy.model import load_buddy_collection
        from able.core.buddy.renderer import render_backpack

        collection = load_buddy_collection()
        text = render_backpack(collection)
        return f"```\n{text}\n```"

    except Exception as e:
        logger.error("buddy_backpack failed: %s", e, exc_info=True)
        return f"Backpack error: {e}"


# ── Registration ──────────────────────────────────────────────────────────────

def register_tools(registry: "ToolRegistry"):
    """Register all buddy tools with the registry."""
    registry.register(
        name="buddy_status",
        definition=BUDDY_STATUS,
        handler=handle_buddy_status,
        display_name="Buddy / Status",
        category="buddy",
        read_only=True,
        concurrent_safe=True,
        surface="buddy",
        artifact_kind="markdown",
        tags=["buddy", "status", "read"],
    )
    registry.register(
        name="buddy_feed",
        definition=BUDDY_FEED,
        handler=handle_buddy_feed,
        display_name="Buddy / Feed",
        category="buddy",
        read_only=False,
        concurrent_safe=False,
        surface="buddy",
        artifact_kind="markdown",
        tags=["buddy", "interaction"],
    )
    registry.register(
        name="buddy_backpack",
        definition=BUDDY_BACKPACK,
        handler=handle_buddy_backpack,
        display_name="Buddy / Backpack",
        category="buddy",
        read_only=True,
        concurrent_safe=True,
        surface="buddy",
        artifact_kind="markdown",
        tags=["buddy", "collection", "read"],
    )
