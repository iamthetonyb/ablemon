"""Tests for the buddy gamification system."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from able.core.buddy.model import (
    BuddyState,
    BuddyStats,
    BuddyNeeds,
    Species,
    Stage,
    CATCH_PROGRESS_TARGET,
    SECRET_SIGNAL_LEVEL,
    STARTER_SPECIES,
    EVOLUTION_REQUIREMENTS,
    LEGENDARY_REQUIREMENTS,
    load_buddy,
    load_buddy_collection,
    list_buddies,
    record_collection_progress,
    reset_buddy_collection,
    save_buddy,
    switch_active_buddy,
    update_collection_profile,
    create_starter_buddy,
    level_from_xp,
    xp_for_level,
)
from able.core.buddy.renderer import (
    render_banner,
    render_backpack,
    render_header,
    render_full,
    render_evolution,
    render_legendary_unlock,
    render_battle_result,
    render_starter_selection,
)
from able.core.buddy.battle import run_battle, list_available_battles
from able.core.buddy.xp import award_interaction_xp, buddy_autonomous_tick
from able.core.buddy.nudge import format_buddy_footer, get_status_line


# ── Model tests ──────────────────────────────────────────────────────────

def test_level_from_xp_starts_at_1():
    assert level_from_xp(0) == 1
    assert level_from_xp(49) == 1


def test_level_progression_is_monotonic():
    prev = 1
    for xp in range(0, 10000, 100):
        lvl = level_from_xp(xp)
        assert lvl >= prev
        prev = lvl


def test_xp_for_level_is_increasing():
    for level in range(1, 50):
        assert xp_for_level(level + 1) > xp_for_level(level)


def test_buddy_state_level_and_progress():
    buddy = BuddyState(name="Ember", species="blaze", xp=500)
    assert buddy.level >= 1
    assert 0 <= buddy.xp_progress_pct <= 100
    assert buddy.xp_to_next > 0


def test_buddy_award_xp():
    buddy = BuddyState(name="Ember", species="blaze", xp=0)
    old_level = buddy.level
    buddy.award_xp(1000)
    assert buddy.xp == 1000
    assert buddy.level >= old_level


def test_create_starter_buddy_can_hatch_shiny(monkeypatch):
    monkeypatch.setattr("able.core.buddy.model.SHINY_STARTER_ODDS", 1)
    buddy = create_starter_buddy(
        name="Ember",
        species=Species.BLAZE,
        created_at="2026-04-02T00:00:00+00:00",
    )
    assert buddy.is_shiny is True
    assert buddy.catch_phrase == buddy.meta["desc"]


def test_evolution_check_starter_to_trained():
    buddy = BuddyState(
        name="Ember",
        species="blaze",
        stage=1,
        xp=xp_for_level(10),
        total_interactions=50,
        eval_passes=5,
    )
    result = buddy.check_evolution()
    assert result == Stage.TRAINED


def test_evolution_check_not_ready():
    buddy = BuddyState(
        name="Ember",
        species="blaze",
        stage=1,
        xp=0,
        total_interactions=5,
        eval_passes=0,
    )
    assert buddy.check_evolution() is None


def test_evolution_trained_to_evolved():
    buddy = BuddyState(
        name="Ember",
        species="blaze",
        stage=2,
        xp=xp_for_level(25),
        distillation_pairs=100,
        evolution_deploys=3,
    )
    result = buddy.check_evolution()
    assert result == Stage.EVOLVED


def test_max_stage_returns_none():
    buddy = BuddyState(name="Ember", species="blaze", stage=3)
    assert buddy.check_evolution() is None


def test_legendary_unlock_requires_real_milestones():
    buddy = BuddyState(
        name="Ember",
        species="blaze",
        stage=Stage.EVOLVED.value,
        xp=xp_for_level(LEGENDARY_REQUIREMENTS["min_level"]),
        eval_passes=LEGENDARY_REQUIREMENTS["min_eval_passes"],
        battles_won=LEGENDARY_REQUIREMENTS["min_battles_won"],
        best_battle_streak=LEGENDARY_REQUIREMENTS["min_battle_streak"],
        distillation_pairs=LEGENDARY_REQUIREMENTS["min_distillation_pairs"],
        evolution_deploys=LEGENDARY_REQUIREMENTS["min_evolution_deploys"],
    )
    title = buddy.unlock_legendary()
    assert title == "Forge Sovereign"
    assert buddy.is_legendary is True
    assert buddy.legendary_unlocked_at


def test_species_meta_accessible():
    for species in Species:
        buddy = BuddyState(name="Test", species=species.value)
        meta = buddy.meta
        assert "emoji" in meta
        assert "label" in meta
        assert "art_stage1" in meta
        assert "bonus_domains" in meta


# ── Persistence tests ────────────────────────────────────────────────────

def test_save_and_load_buddy(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "able.core.buddy.model.BUDDY_PATH", tmp_path / "buddy.yaml"
    )
    monkeypatch.setattr(
        "able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "buddy_collection.yaml"
    )
    buddy = BuddyState(
        name="Volt",
        species="spark",
        stage=2,
        xp=1500,
        battles_won=3,
        catch_phrase="Words are ammo.",
        created_at="2026-04-01T00:00:00Z",
        is_shiny=True,
        legendary_title="Storm Scribe",
        legendary_unlocked_at="2026-04-01T01:00:00Z",
        current_battle_streak=2,
        best_battle_streak=4,
    )
    save_buddy(buddy)

    loaded = load_buddy()
    assert loaded is not None
    assert loaded.name == "Volt"
    assert loaded.species == "spark"
    assert loaded.stage == 2
    assert loaded.xp == 1500
    assert loaded.battles_won == 3
    assert loaded.catch_phrase == "Words are ammo."
    assert loaded.is_shiny is True
    assert loaded.legendary_title == "Storm Scribe"
    assert loaded.legendary_unlocked_at == "2026-04-01T01:00:00Z"
    assert loaded.current_battle_streak == 2
    assert loaded.best_battle_streak == 4


def test_load_buddy_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "able.core.buddy.model.BUDDY_PATH", tmp_path / "nope.yaml"
    )
    monkeypatch.setattr(
        "able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "nope_collection.yaml"
    )
    assert load_buddy() is None


def test_record_collection_progress_unlocks_new_species(tmp_path, monkeypatch):
    monkeypatch.setattr("able.core.buddy.model.BUDDY_PATH", tmp_path / "buddy.yaml")
    monkeypatch.setattr("able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "buddy_collection.yaml")

    starter = create_starter_buddy(
        name="Ember",
        species=Species.BLAZE,
        created_at="2026-04-02T00:00:00+00:00",
    )
    save_buddy(starter)

    update = record_collection_progress("security", points=CATCH_PROGRESS_TARGET)
    collection = load_buddy_collection()

    assert collection is not None
    assert any(buddy.species == "phantom" for buddy in collection.list_buddies())
    assert len(update["new_buddies"]) == 1
    assert update["new_buddies"][0].species == "phantom"


def test_switch_active_buddy_changes_active_species(tmp_path, monkeypatch):
    monkeypatch.setattr("able.core.buddy.model.BUDDY_PATH", tmp_path / "buddy.yaml")
    monkeypatch.setattr("able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "buddy_collection.yaml")

    blaze = BuddyState(name="Ember", species="blaze")
    wave = BuddyState(name="Current", species="wave")
    save_buddy(blaze)
    save_buddy(wave)

    switched = switch_active_buddy("wave")
    active = load_buddy()

    assert switched is not None
    assert active is not None
    assert active.species == "wave"


def test_update_collection_profile_persists_operator_setup(tmp_path, monkeypatch):
    monkeypatch.setattr("able.core.buddy.model.BUDDY_PATH", tmp_path / "buddy.yaml")
    monkeypatch.setattr("able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "buddy_collection.yaml")

    save_buddy(BuddyState(name="Ember", species="blaze"))
    collection = update_collection_profile(
        {
            "focus": "coding",
            "work_style": "solo-operator",
            "distillation_track": "9b-fast-local",
            "completed_at": "2026-04-02T00:00:00+00:00",
        }
    )

    assert collection.operator_profile["focus"] == "coding"
    loaded = load_buddy_collection()
    assert loaded is not None
    assert loaded.operator_profile["distillation_track"] == "9b-fast-local"


def test_reset_buddy_collection_removes_persisted_files(tmp_path, monkeypatch):
    monkeypatch.setattr("able.core.buddy.model.BUDDY_PATH", tmp_path / "buddy.yaml")
    monkeypatch.setattr("able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "buddy_collection.yaml")

    save_buddy(BuddyState(name="Ember", species="blaze"))
    reset_buddy_collection()

    assert load_buddy() is None
    assert load_buddy_collection() is None


def test_full_collection_unlocks_badges_and_easter_egg(tmp_path, monkeypatch):
    monkeypatch.setattr("able.core.buddy.model.BUDDY_PATH", tmp_path / "buddy.yaml")
    monkeypatch.setattr("able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "buddy_collection.yaml")

    for species in STARTER_SPECIES:
        buddy = BuddyState(
            name=species.value.capitalize(),
            species=species.value,
            stage=Stage.EVOLVED.value,
            xp=xp_for_level(LEGENDARY_REQUIREMENTS["min_level"]),
            eval_passes=LEGENDARY_REQUIREMENTS["min_eval_passes"],
            battles_won=LEGENDARY_REQUIREMENTS["min_battles_won"],
            best_battle_streak=LEGENDARY_REQUIREMENTS["min_battle_streak"],
            distillation_pairs=LEGENDARY_REQUIREMENTS["min_distillation_pairs"],
            evolution_deploys=LEGENDARY_REQUIREMENTS["min_evolution_deploys"],
            legendary_title="Unlocked",
        )
        save_buddy(buddy)

    collection = load_buddy_collection()
    assert collection is not None
    badge_ids = {badge["id"] for badge in collection.badges}
    assert "full-dex" in badge_ids
    assert "sixth-signal" in badge_ids
    assert "evolution-league" in badge_ids
    assert "legendary-league" in badge_ids
    assert "master-trainer" in badge_ids
    assert collection.easter_egg_title
    assert any(buddy.species == "aether" for buddy in collection.list_buddies())


def test_secret_signal_crown_unlocks_after_hidden_buddy_mastery(tmp_path, monkeypatch):
    monkeypatch.setattr("able.core.buddy.model.BUDDY_PATH", tmp_path / "buddy.yaml")
    monkeypatch.setattr("able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "buddy_collection.yaml")

    for species in STARTER_SPECIES:
        starter = BuddyState(
            name=species.value.capitalize(),
            species=species.value,
            stage=Stage.EVOLVED.value,
            xp=xp_for_level(LEGENDARY_REQUIREMENTS["min_level"]),
            eval_passes=LEGENDARY_REQUIREMENTS["min_eval_passes"],
            battles_won=LEGENDARY_REQUIREMENTS["min_battles_won"],
            best_battle_streak=LEGENDARY_REQUIREMENTS["min_battle_streak"],
            distillation_pairs=LEGENDARY_REQUIREMENTS["min_distillation_pairs"],
            evolution_deploys=LEGENDARY_REQUIREMENTS["min_evolution_deploys"],
            legendary_title="Unlocked",
        )
        save_buddy(starter)

    secret = next(
        buddy for buddy in load_buddy_collection().list_buddies()  # type: ignore[union-attr]
        if buddy.species == "aether"
    )
    secret.stage = Stage.EVOLVED.value
    secret.xp = xp_for_level(SECRET_SIGNAL_LEVEL)
    secret.legendary_title = "Prime Orchestrator"
    save_buddy(secret)

    collection = load_buddy_collection()
    assert collection is not None
    badge_ids = {badge["id"] for badge in collection.badges}
    assert "signal-crown" in badge_ids


def test_trainer_badge_unlocks_on_first_evolution(tmp_path, monkeypatch):
    monkeypatch.setattr("able.core.buddy.model.BUDDY_PATH", tmp_path / "buddy.yaml")
    monkeypatch.setattr("able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "buddy_collection.yaml")

    save_buddy(BuddyState(name="Ember", species="blaze", stage=Stage.TRAINED.value))
    collection = load_buddy_collection()
    assert collection is not None
    badge_ids = {badge["id"] for badge in collection.badges}
    assert "trainer" in badge_ids


# ── Renderer tests ───────────────────────────────────────────────────────

def test_render_banner_contains_name_and_level():
    buddy = BuddyState(name="Ember", species="blaze", xp=500, battles_won=2)
    banner = render_banner(buddy)
    assert "Ember" in banner
    assert "Lv." in banner
    assert "Wins:2" in banner


def test_render_header_shows_buddy_art_and_stats():
    buddy = BuddyState(name="Ember", species="blaze", xp=500, battles_won=2)
    header = render_header(buddy, provider_count=8)
    assert "Ember" in header
    assert "ABLE" in header
    assert "8 AI providers ready" in header
    assert "Lv." in header
    assert "Wins 2" in header
    # Should contain ASCII art characters from blaze stage 1
    assert "\u25c9" in header or "\u256d" in header or "\u2502" in header


def test_render_full_contains_all_sections():
    buddy = BuddyState(
        name="Shade",
        species="phantom",
        stage=2,
        xp=2000,
        catch_phrase="Sealing cracks.",
        total_interactions=80,
        eval_passes=10,
    )
    output = render_full(buddy)
    assert "Shade" in output
    assert "Phantom" in output
    assert "Sealing cracks." in output
    assert "Trained" in output
    assert "80" in output
    assert "Type:" in output
    assert "Abilities:" in output


def test_render_full_shows_rarity_and_legendary():
    buddy = BuddyState(
        name="Ember",
        species="blaze",
        stage=3,
        xp=xp_for_level(45),
        is_shiny=True,
        legendary_title="Forge Sovereign",
    )
    output = render_full(buddy)
    assert "Legendary Shiny" in output
    assert "Forge Sovereign" in output


def test_render_starter_selection_lists_all_species():
    output = render_starter_selection()
    assert "Blaze" in output
    assert "Wave" in output
    assert "Root" in output
    assert "Spark" in output
    assert "Phantom" in output
    assert "Aether" not in output


def test_render_starter_selection_explains_roles_and_routing_scope():
    output = render_starter_selection()
    assert "This affects buddy theme + bonus XP only" in output
    assert "Fire" in output
    assert "Water" in output
    assert "Earth" in output
    assert "Lightning" in output
    assert "Shadow" in output
    assert "Best for:" in output


def test_render_backpack_shows_owned_and_uncaught_species(tmp_path, monkeypatch):
    monkeypatch.setattr("able.core.buddy.model.BUDDY_PATH", tmp_path / "buddy.yaml")
    monkeypatch.setattr("able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "buddy_collection.yaml")
    save_buddy(BuddyState(name="Ember", species="blaze"))
    update_collection_profile(
        {
            "focus": "coding",
            "work_style": "all-terrain",
            "distillation_track": "9b-fast-local",
            "completed_at": "2026-04-02T00:00:00+00:00",
        }
    )
    output = render_backpack(load_buddy_collection())
    assert "Buddy Backpack" in output
    assert "Caught: 1/5 starters" in output
    assert "Operator profile" in output
    assert "All-terrain" in output
    assert "9B fast local" in output
    assert "Uncaught" in output
    assert "Collection Bonus" not in output


def test_render_evolution_announcement():
    buddy = BuddyState(name="Ember", species="blaze", stage=1)
    buddy.evolve(Stage.TRAINED)
    output = render_evolution(buddy, Stage.STARTER, Stage.TRAINED)
    assert "EVOLVING" in output
    assert "Ember" in output
    assert "Starter  -->  Trained" in output
    assert "Trained" in output


def test_render_legendary_unlock():
    buddy = BuddyState(
        name="Ember",
        species="blaze",
        stage=3,
        legendary_title="Forge Sovereign",
    )
    output = render_legendary_unlock(buddy)
    assert "legendary form" in output.lower()
    assert "Forge Sovereign" in output


def test_render_battle_result_shows_outcome():
    buddy = BuddyState(name="Ember", species="blaze")
    output = render_battle_result(buddy, "security", 6, 7, "win", 50)
    assert "VICTORY" in output
    assert "security" in output.lower()
    assert "+50 XP" in output


# ── Battle tests ─────────────────────────────────────────────────────────

def test_battle_dry_run_returns_record():
    buddy = BuddyState(name="Ember", species="blaze")
    record = run_battle(buddy, "security", dry_run=True)
    assert record is not None
    assert record.domain == "security"
    assert record.result in ("win", "draw", "loss")
    assert record.xp_earned > 0
    assert record.total == 7


def test_battle_unknown_domain_returns_none():
    buddy = BuddyState(name="Ember", species="blaze")
    assert run_battle(buddy, "nonexistent_domain") is None


def test_list_available_battles_resolves_from_repo_root(monkeypatch, tmp_path):
    old_cwd = Path.cwd()
    monkeypatch.chdir(tmp_path)
    try:
        available = list_available_battles()
    finally:
        os.chdir(old_cwd)
    assert "reasoning" in available


def test_battle_record_updates_buddy():
    from able.core.buddy.model import BattleRecord
    from datetime import datetime, timezone

    buddy = BuddyState(name="Ember", species="blaze")
    record = BattleRecord(
        domain="code",
        score_pct=85.0,
        passed=6,
        total=7,
        result="win",
        xp_earned=50,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    buddy.record_battle(record)
    assert buddy.battles_won == 1
    assert buddy.current_battle_streak == 1
    assert buddy.best_battle_streak == 1
    assert buddy.xp == 50
    assert len(buddy.battle_log) == 1


def test_battle_loss_resets_streak():
    from able.core.buddy.model import BattleRecord
    from datetime import datetime, timezone

    buddy = BuddyState(name="Ember", species="blaze", current_battle_streak=2, best_battle_streak=2)
    record = BattleRecord(
        domain="security",
        score_pct=20.0,
        passed=1,
        total=5,
        result="loss",
        xp_earned=5,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    buddy.record_battle(record)
    assert buddy.current_battle_streak == 0
    assert buddy.best_battle_streak == 2


# ── XP engine tests ─────────────────────────────────────────────────────

def test_award_interaction_xp_returns_none_without_buddy(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "able.core.buddy.xp.load_buddy", lambda: None
    )
    assert award_interaction_xp(complexity_score=0.5) is None


def test_award_interaction_xp_with_buddy(tmp_path, monkeypatch):
    buddy = BuddyState(name="Ember", species="blaze", xp=0, created_at="2026-01-01")
    saved = [False]

    def fake_load():
        return buddy

    def fake_save(b):
        saved[0] = True

    monkeypatch.setattr("able.core.buddy.xp.load_buddy", fake_load)
    monkeypatch.setattr("able.core.buddy.xp.save_buddy", fake_save)

    result = award_interaction_xp(
        complexity_score=0.7,
        used_tools=True,
        domain="coding",  # Blaze bonus domain
    )
    assert result is not None
    assert result["xp"] > 10  # Base + complexity + tool + domain bonus
    assert "buddy_name" in result
    assert "mood" in result
    assert saved[0] is True


def test_award_interaction_xp_aether_gets_orchestration_bonus(tmp_path, monkeypatch):
    buddy = BuddyState(name="Aether", species="aether", xp=0, created_at="2026-01-01")

    monkeypatch.setattr("able.core.buddy.xp.load_buddy", lambda: buddy)
    monkeypatch.setattr("able.core.buddy.xp.save_buddy", lambda current: None)

    result = award_interaction_xp(
        complexity_score=0.8,
        used_tools=True,
        domain="default",
        selected_tier=4,
    )

    assert result["xp"] == 43


# ── Buddy footer tests ──────────────────────────────────────────────────


def test_format_buddy_footer_empty():
    assert format_buddy_footer(None) == ""
    assert format_buddy_footer({}) == ""


def test_format_buddy_footer_normal():
    result = format_buddy_footer({
        "xp": 14, "leveled_up": False, "level": 5, "old_level": 5,
        "evolved": None, "legendary": None,
        "buddy_name": "Atlas", "buddy_emoji": "\U0001f33f", "mood": "thriving",
    })
    assert "Atlas" in result
    assert "Lv5" in result
    assert "+14 XP" in result
    assert "thriving" in result


def test_format_buddy_footer_level_up():
    result = format_buddy_footer({
        "xp": 14, "leveled_up": True, "level": 6, "old_level": 5,
        "evolved": None, "legendary": None,
        "buddy_name": "Atlas", "buddy_emoji": "\U0001f33f", "mood": "thriving",
    })
    assert "leveled up" in result
    assert "Lv5" in result
    assert "Lv6" in result


def test_format_buddy_footer_evolution():
    result = format_buddy_footer({
        "xp": 14, "leveled_up": False, "level": 10, "old_level": 10,
        "evolved": 2, "legendary": None,
        "buddy_name": "Atlas", "buddy_emoji": "\U0001f33f", "mood": "thriving",
    })
    assert "EVOLVED" in result
    assert "Stage 2" in result


def test_format_buddy_footer_legendary():
    result = format_buddy_footer({
        "xp": 14, "leveled_up": False, "level": 20, "old_level": 20,
        "evolved": None, "legendary": "Ancient Root",
        "buddy_name": "Atlas", "buddy_emoji": "\U0001f33f", "mood": "thriving",
    })
    assert "legendary" in result
    assert "Ancient Root" in result


def test_get_status_line_no_buddy(monkeypatch):
    monkeypatch.setattr("able.core.buddy.nudge.load_buddy", lambda: None)
    assert get_status_line() == ""


# ── Needs / Tamagotchi tests ──────────────────────────────────────────────

def test_buddy_needs_defaults():
    needs = BuddyNeeds()
    assert needs.hunger == 80.0
    assert needs.thirst == 80.0
    assert needs.energy == 80.0
    assert needs.mood == "thriving"


def test_needs_decay():
    needs = BuddyNeeds(hunger=80, thirst=80, energy=80)
    needs.decay(10)  # 10 hours
    assert needs.hunger == 60.0   # 80 - 2.0*10
    assert needs.thirst == 65.0   # 80 - 1.5*10
    assert needs.energy == 70.0   # 80 - 1.0*10


def test_needs_decay_floors_at_zero():
    needs = BuddyNeeds(hunger=5, thirst=5, energy=5)
    needs.decay(100)
    assert needs.hunger == 0
    assert needs.thirst == 0
    assert needs.energy == 0


def test_needs_feed_restores_hunger():
    needs = BuddyNeeds(hunger=40)
    restored = needs.feed("battle")
    assert restored == 30  # NEED_RESTORE["hunger"]["battle"]
    assert needs.hunger == 70


def test_needs_feed_caps_at_100():
    needs = BuddyNeeds(hunger=90)
    needs.feed("battle")
    assert needs.hunger == 100


def test_needs_water_restores_thirst():
    needs = BuddyNeeds(thirst=30)
    restored = needs.water("evolve")
    assert restored == 40  # NEED_RESTORE["thirst"]["evolve"]
    assert needs.thirst == 70


def test_needs_walk_restores_energy():
    needs = BuddyNeeds(energy=50)
    restored = needs.walk("new_domain")
    assert restored == 25  # NEED_RESTORE["energy"]["new_domain"]
    assert needs.energy == 75


def test_mood_thriving():
    needs = BuddyNeeds(hunger=80, thirst=80, energy=80)
    assert needs.mood == "thriving"


def test_mood_content():
    needs = BuddyNeeds(hunger=60, thirst=60, energy=60)
    assert needs.mood == "content"


def test_mood_hungry():
    needs = BuddyNeeds(hunger=20, thirst=80, energy=80)
    assert needs.mood == "hungry"


def test_mood_neglected():
    needs = BuddyNeeds(hunger=5, thirst=80, energy=80)
    assert needs.mood == "neglected"


def test_buddy_state_get_needs():
    buddy = BuddyState(name="Ember", species="blaze", needs_hunger=60, needs_thirst=50, needs_energy=40)
    needs = buddy.get_needs()
    assert needs.hunger == 60
    assert needs.thirst == 50
    assert needs.energy == 40


def test_buddy_state_feed():
    buddy = BuddyState(name="Ember", species="blaze", needs_hunger=50)
    restored = buddy.feed("battle")
    assert restored == 30
    assert buddy.needs_hunger == 80


def test_buddy_state_water():
    buddy = BuddyState(name="Ember", species="blaze", needs_thirst=40)
    restored = buddy.water("evolve")
    assert restored == 40
    assert buddy.needs_thirst == 80


def test_buddy_state_walk_new_domain():
    buddy = BuddyState(name="Ember", species="blaze", needs_energy=50)
    restored = buddy.walk("new_domain", domain="security")
    assert restored == 25
    assert buddy.needs_energy == 75
    assert "security" in buddy.domains_used_today


def test_buddy_state_walk_variety_bonus():
    buddy = BuddyState(name="Ember", species="blaze", needs_energy=20)
    buddy.walk("new_domain", domain="code")
    buddy.walk("new_domain", domain="research")
    buddy.walk("new_domain", domain="security")  # 3rd domain triggers variety bonus
    assert len(buddy.domains_used_today) == 3
    # Energy should have gotten new_domain + variety on the 3rd walk
    assert buddy.needs_energy > 20 + 25 + 25  # base + 2 new_domains, then 3rd gets bonus


def test_buddy_apply_needs_decay():
    from datetime import datetime, timezone, timedelta
    past = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    buddy = BuddyState(name="Ember", species="blaze", needs_hunger=80, needs_last_decay=past)
    mood = buddy.apply_needs_decay()
    assert buddy.needs_hunger < 80  # Should have decayed
    assert mood in ("thriving", "content", "hungry", "neglected")
    assert buddy.needs_last_decay != past  # Updated


def test_needs_persist_through_save_load(tmp_path, monkeypatch):
    monkeypatch.setattr("able.core.buddy.model.BUDDY_PATH", tmp_path / "buddy.yaml")
    monkeypatch.setattr("able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "buddy_collection.yaml")
    buddy = BuddyState(
        name="Ember", species="blaze",
        needs_hunger=42.5, needs_thirst=65.0, needs_energy=88.0,
        needs_last_decay="2026-04-01T00:00:00+00:00",
        domains_used_today=["code", "security"],
        domains_today_date="2026-04-01",
    )
    save_buddy(buddy)
    loaded = load_buddy()
    assert loaded is not None
    assert loaded.needs_hunger == 42.5
    assert loaded.needs_thirst == 65.0
    assert loaded.needs_energy == 88.0
    assert loaded.needs_last_decay == "2026-04-01T00:00:00+00:00"
    assert loaded.domains_used_today == ["code", "security"]
    assert loaded.domains_today_date == "2026-04-01"


# ── Autonomous tick tests ───────────────────────────────────────────────

def test_autonomous_tick_returns_none_without_buddy(tmp_path, monkeypatch):
    monkeypatch.setattr("able.core.buddy.model.BUDDY_PATH", tmp_path / "nope.yaml")
    monkeypatch.setattr("able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "nope_c.yaml")
    assert buddy_autonomous_tick() is None


def test_autonomous_tick_awards_passive_xp(tmp_path, monkeypatch):
    monkeypatch.setattr("able.core.buddy.model.BUDDY_PATH", tmp_path / "buddy.yaml")
    monkeypatch.setattr("able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "buddy_c.yaml")
    buddy = create_starter_buddy(
        name="Volt", species=Species.SPARK,
        created_at="2026-04-01T00:00:00+00:00",
    )
    save_buddy(buddy)
    old_xp = buddy.xp

    result = buddy_autonomous_tick()
    assert result is not None
    assert result["name"] == "Volt"
    assert result["xp"] > old_xp
    assert result["mood"] in ("thriving", "content", "hungry", "neglected")


def test_autonomous_tick_restores_energy(tmp_path, monkeypatch):
    monkeypatch.setattr("able.core.buddy.model.BUDDY_PATH", tmp_path / "buddy.yaml")
    monkeypatch.setattr("able.core.buddy.model.BUDDY_COLLECTION_PATH", tmp_path / "buddy_c.yaml")
    buddy = create_starter_buddy(
        name="Root", species=Species.ROOT,
        created_at="2026-04-01T00:00:00+00:00",
    )
    buddy.needs_energy = 30.0
    save_buddy(buddy)

    buddy_autonomous_tick()

    reloaded = load_buddy()
    assert reloaded is not None
    # Energy should have been restored by the self_explore walk
    assert reloaded.needs_energy > 30.0
