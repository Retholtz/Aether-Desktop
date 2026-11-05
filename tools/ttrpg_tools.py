"""
tools/ttrpg_tools.py - Agent Tools for Tabletop Adjudication & DM Document Grounding
Exposes mathematical dice rolling, vitals modification, turn ladder control,
campaign document review (rulesets, modules, character sheets), and roster character sync.
"""
from typing import Dict, Any, Optional
from core.ttrpg_service import TTRPGService, level_up_dnd_character


def register_ttrpg_tools(dispatcher, service: TTRPGService, notify_ui_callback=None):

    def emit_update(event_name: str, payload: dict):
        if callable(notify_ui_callback):
            try:
                notify_ui_callback(event_name, payload)
                return
            except Exception:
                pass
        if callable(getattr(dispatcher, "notify", None)):
            try:
                dispatcher.notify(event_name, payload)
                return
            except Exception:
                pass
        if callable(getattr(dispatcher, "on_event", None)):
            try:
                dispatcher.on_event(event_name, payload)
            except Exception:
                pass

    async def roll_dice(formula: str, roll_type: str = "normal", purpose: Optional[str] = None) -> Dict[str, Any]:
        """
        Executes a cryptographically fair dice roll.
        Formula must be in standard notation like '1d20+5', '2d6', or '8d6+2'.
        roll_type must be one of: 'normal', 'advantage', 'disadvantage'.
        """
        res = service.parse_and_roll(formula, roll_type)
        if res.get("status") == "success" and purpose:
            res["purpose"] = purpose
        if res.get("status") == "success":
            emit_update("dice_roll_event", res)
        return res

    async def modify_character_vitals(
        character_name: str,
        stat_name: str,
        amount: int,
        is_custom_meter: bool = False
    ) -> Dict[str, Any]:
        """
        Alters character vitals (e.g. 'health', 'guard') or custom meters (e.g. 'mana', 'sanity').
        Use negative integers for damage/cost, positive for healing/gain.
        """
        res = service.modify_character_stat(
            character_name=character_name,
            stat_key=stat_name.lower().strip(),
            delta=amount,
            is_custom_meter=is_custom_meter
        )
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
        return res

    async def advance_combat_turn() -> Dict[str, Any]:
        """
        Advances the initiative tracker to the next turn or round in active combat.
        Returns the active character who is currently taking their turn.
        """
        res = service.advance_turn()
        if res.get("status") == "success":
            emit_update("initiative_updated", res)
        return res

    async def set_character_condition(character_name: str, condition: str, is_active: bool) -> Dict[str, Any]:
        """Applies or clears a status condition (e.g., Poisoned, Stunned, Fatigued) on a character."""
        res = service.update_condition(character_name, condition, is_active)
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
        return res

    async def list_campaign_documents() -> Dict[str, Any]:
        """
        Lists all uploaded rulesets, campaign modules, character sheets, and lore documents
        in the active Role Play campaign.
        """
        return service.list_campaign_documents()

    async def read_campaign_document(filename: str = "all", query: Optional[str] = None) -> Dict[str, Any]:
        """
        Reads the extracted text of an uploaded campaign document (campaign adventure module, ruleset,
        or campaign journal notes) so Aether can review it and act as the Dungeon Master / Game Master.
        Active party characters are maintained directly in the Party Roster.
        Pass query to search for a specific scene, chapter, or NPC (e.g. 'Scene 1', 'Prologue', 'Chapter 1', or an NPC name).
        Pass filename='all' to read all uploaded documents in the active campaign.
        """
        return service.read_campaign_document(filename=filename, query=query)

    async def lookup_ruleset_rule(topic: str) -> Dict[str, Any]:
        """
        Looks up a specific rule, condition, combat action, or custom house rule supplement on the fly
        without cluttering the main conversation context.
        """
        return service.lookup_ruleset_rule(topic=topic)

    async def upsert_ttrpg_character(
        name: str,
        player_name: str = "Player",
        archetype: str = "Adventurer",
        max_health: int = 25,
        current_health: Optional[int] = None,
        guard: int = 14,
        custom_meter_label: str = "Stamina",
        custom_meter_max: int = 5,
        might: int = 10,
        agility: int = 10,
        vigor: int = 10,
        intellect: int = 10,
        presence: int = 10
    ) -> Dict[str, Any]:
        """
        Creates or updates a character sheet on the active Role Play Party Roster.
        Use this when creating a new character, adding an NPC ally, or when a player introduces a new hero.
        Do NOT call this during active play to overwrite existing player characters whose stats have evolved.
        """
        payload = {
            "name": name,
            "player_name": player_name,
            "archetype": archetype,
            "max_health": max_health,
            "current_health": current_health if current_health is not None else max_health,
            "guard": guard,
            "custom_meter_label": custom_meter_label,
            "custom_meter_max": custom_meter_max,
            "Might": might,
            "Agility": agility,
            "Vigor": vigor,
            "Intellect": intellect,
            "Presence": presence
        }
        res = service.add_character(payload)
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
        return res

    async def manage_character_progression(
        character_name: str,
        action: str = "award_xp",
        amount_or_level: int = 0,
        notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Awards experience points (XP) or levels up a character, adjusting Max HP, hit dice, and proficiency bonuses while guiding the player.
        """
        res = service.manage_character_progression(
            character_name=character_name,
            action=action,
            amount_or_level=amount_or_level,
            notes=notes
        )
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
        return res

    async def update_campaign_details(
        title: Optional[str] = None,
        system_preset: Optional[str] = None,
        setting_overview: Optional[str] = None,
        tone: Optional[str] = None,
        difficulty: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Updates the active campaign's title, system preset, setting overview, narrative tone, or difficulty guidance mode.
        """
        res = service.update_campaign(
            title=title,
            system_preset=system_preset,
            setting_overview=setting_overview,
            tone=tone,
            difficulty=difficulty
        )
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
        return res

    async def record_campaign_notes(
        summary: str,
        location: Optional[str] = None,
        key_clues: Optional[List[str]] = None,
        active_quest: Optional[str] = None,
        last_action: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Records DM campaign notes, scene progression, discovered clues, and last action taken into the persistent campaign journal.
        Always call this after key story beats, discovered clues, or at the end of a scene/session so Aether retains full memory across restarts.
        """
        res = service.record_campaign_notes(
            summary=summary,
            location=location,
            key_clues=key_clues,
            active_quest=active_quest,
            last_action=last_action
        )
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
        return res

    async def get_campaign_notes() -> Dict[str, Any]:
        """
        Retrieves the persistent campaign journal, latest turn recap, discovered clues, and active quest objective.
        Use this when players ask for a summary, 'where were we?', or to check discovered clues.
        """
        return service.get_campaign_notes()

    dispatcher.register_tool(
        name="roll_dice",
        description="Rolls fair dice using standard notation (e.g., '1d20+5', '2d6') with support for advantage and disadvantage.",
        parameters={
            "type": "object",
            "properties": {
                "formula": {"type": "string", "description": "Dice expression (e.g., '1d20+7', '3d6')."},
                "roll_type": {"type": "string", "enum": ["normal", "advantage", "disadvantage"], "description": "Roll modifier."},
                "purpose": {"type": "string", "description": "Context of roll (e.g., 'Kaelen Athletics Check', 'Fireball Damage')."}
            },
            "required": ["formula"]
        },
        handler=roll_dice
    )

    dispatcher.register_tool(
        name="modify_character_vitals",
        description="Updates health, armor defense, or custom meters (sanity, mana, stress) for a player or NPC.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "Target character name."},
                "stat_name": {"type": "string", "description": "Metric name ('health', 'guard', 'stamina')."},
                "amount": {"type": "integer", "description": "Signed integer to apply (negative for damage)."},
                "is_custom_meter": {"type": "boolean", "description": "True if targeting a custom sandbox meter."}
            },
            "required": ["character_name", "stat_name", "amount"]
        },
        handler=modify_character_vitals
    )

    dispatcher.register_tool(
        name="advance_combat_turn",
        description="Advances initiative to the next character in turn order.",
        parameters={"type": "object", "properties": {}},
        handler=advance_combat_turn
    )

    dispatcher.register_tool(
        name="set_character_condition",
        description="Adds or removes an ongoing status condition on a character.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "Target character."},
                "condition": {"type": "string", "description": "Condition name (e.g., 'Blinded', 'Prone')."},
                "is_active": {"type": "boolean", "description": "True to add, False to clear."}
            },
            "required": ["character_name", "condition", "is_active"]
        },
        handler=set_character_condition
    )

    dispatcher.register_tool(
        name="list_campaign_documents",
        description="Lists all uploaded rulesets, campaign modules, character sheets, and notes in the active TTRPG campaign.",
        parameters={"type": "object", "properties": {}},
        handler=list_campaign_documents
    )

    dispatcher.register_tool(
        name="read_campaign_document",
        description="Reads extracted text from an uploaded TTRPG campaign document (ruleset, campaign module, or character sheet) so Aether can review it and act as the Dungeon Master / Game Master. Pass query to search for a specific scene or NPC (e.g. 'Scene 1', 'Prologue', 'Chapter 1', or an NPC name). Pass filename='all' to review all uploaded documents.",
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename of the uploaded document (e.g., 'adventure_module.pdf', 'dnd_rules.pdf', 'char_sheet.pdf'), category ('ruleset', 'campaign', 'character_sheet'), or 'all' to read all uploaded documents."
                },
                "query": {
                    "type": "string",
                    "description": "Optional search term, scene name, or NPC name (e.g., 'Scene 1', 'Prologue', 'Tavern') to retrieve only the relevant excerpt."
                }
            },
            "required": ["filename"]
        },
        handler=read_campaign_document
    )

    dispatcher.register_tool(
        name="upsert_ttrpg_character",
        description="Creates or updates a player or NPC character sheet on the active Role Play Party Roster (e.g. after reviewing an uploaded character sheet).",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Character name."},
                "player_name": {"type": "string", "description": "Player name."},
                "archetype": {"type": "string", "description": "Class, archetype, or role (e.g., 'Level 5 Ranger', 'Frontline Warden')."},
                "max_health": {"type": "integer", "description": "Maximum hit points / health."},
                "current_health": {"type": "integer", "description": "Current hit points / health."},
                "guard": {"type": "integer", "description": "Armor Class (AC) / Defense / Guard rating."},
                "custom_meter_label": {"type": "string", "description": "Label for custom resource meter (e.g., 'Spell Slots', 'Stamina', 'Sanity', 'Ki Points')."},
                "custom_meter_max": {"type": "integer", "description": "Maximum value for the custom meter."},
                "might": {"type": "integer", "description": "Strength / Might score."},
                "agility": {"type": "integer", "description": "Dexterity / Agility score."},
                "vigor": {"type": "integer", "description": "Constitution / Vigor score."},
                "intellect": {"type": "integer", "description": "Intelligence / Intellect score."},
                "presence": {"type": "integer", "description": "Charisma / Wisdom / Presence score."}
            },
            "required": ["name"]
        },
        handler=upsert_ttrpg_character
    )

    dispatcher.register_tool(
        name="update_campaign_details",
        description="Updates the active TTRPG campaign's title, system preset, setting overview, or narrative tone.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Updated campaign title."},
                "system_preset": {
                    "type": "string",
                    "enum": ["heroic_d20", "investigative_d100", "cybernetic_action", "narrative_clocks", "sandbox_custom"],
                    "description": "System ruleset archetype."
                },
                "setting_overview": {"type": "string", "description": "World setting and premise overview."},
                "tone": {"type": "string", "description": "Narrative tone (e.g., 'Epic high fantasy', 'Gritty gothic horror')."}
            }
        },
        handler=update_campaign_details
    )

    dispatcher.register_tool(
        name="record_campaign_notes",
        description="Records DM campaign notes, scene progression, discovered clues, and last action taken into the persistent campaign journal so memory is retained across restarts.",
        parameters={
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Concise 2-3 sentence summary of what the party did, discovered, or where they left off."},
                "location": {"type": "string", "description": "Current party location or setting (e.g., 'Camp Highmore', 'Ambushed Caravan at Chult Jungle Edge')."},
                "key_clues": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of key items, clues, or observations discovered."
                },
                "active_quest": {"type": "string", "description": "Current primary goal or active quest objective."},
                "last_action": {"type": "string", "description": "Exact last action taken by the party."}
            },
            "required": ["summary"]
        },
        handler=record_campaign_notes
    )

    dispatcher.register_tool(
        name="get_campaign_notes",
        description="Retrieves the persistent campaign journal, latest turn recap, discovered clues, and quest state when players ask for a summary or recap.",
        parameters={
            "type": "object",
            "properties": {}
        },
        handler=get_campaign_notes
    )

    dispatcher.register_tool(
        name="lookup_ruleset_rule",
        description="Looks up a specific rule, condition, combat mechanic, or custom house rule supplement on the fly (e.g. 'grappling', 'cover', 'suffocation', 'death saves', 'custom supplement') without loading entire rulebooks into memory.",
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "The specific rule, mechanic, spell, or condition keyword to look up."
                }
            },
            "required": ["topic"]
        },
        handler=lookup_ruleset_rule
    )

    dispatcher.register_tool(
        name="manage_character_progression",
        description="Awards experience points (XP) to a character or levels them up (e.g. after defeating monsters, completing quest objectives, or achieving milestones), updating Max HP and stats while guiding the player.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {
                    "type": "string",
                    "description": "The name of the character to progress."
                },
                "action": {
                    "type": "string",
                    "enum": ["award_xp", "level_up"],
                    "description": "Whether to award XP or level up."
                },
                "amount_or_level": {
                    "type": "integer",
                    "description": "XP amount to grant (if action='award_xp') or target level number (if action='level_up')."
                },
                "notes": {
                    "type": "string",
                    "description": "Context or reason (e.g. 'Defeated elven scout ambush', 'Milestone completed')."
                }
            },
            "required": ["character_name", "action"]
        },
        handler=manage_character_progression
    )

    async def manage_party_inventory(
        character_name: str,
        action: str = "add",
        items: Optional[List[str]] = None,
        weapons: Optional[List[Dict[str, str]]] = None,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        res = service.manage_character_inventory(
            character_name=character_name,
            action=action,
            items=items,
            weapons=weapons,
            reason=reason
        )
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
        return res

    dispatcher.register_tool(
        name="manage_party_inventory",
        description="Adds, removes, or uses items and weapons for a party member (e.g. when looting chests, finding weapons, drinking healing potions, spending rations, dropping gear, or losing equipment).",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {
                    "type": "string",
                    "description": "Name of the character whose inventory or equipment is changing."
                },
                "action": {
                    "type": "string",
                    "enum": ["add", "remove", "use"],
                    "description": "Whether items/weapons are being added (looted/bought), removed (discarded/lost), or used (potions/scrolls consumed)."
                },
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of item names to add or remove (e.g. ['Potion of Healing', '50 ft. Rope', 'Gold pouch'])."
                },
                "weapons": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Weapon name, e.g. 'Dual Daggers' or 'Longsword'."},
                            "atk_bonus": {"type": "string", "description": "Attack bonus modifier, e.g. '+4'."},
                            "damage": {"type": "string", "description": "Damage formula, e.g. '1d4+2 Piercing'."},
                            "notes": {"type": "string", "description": "Weapon properties, e.g. 'Finesse, Light'."}
                        },
                        "required": ["name"]
                    },
                    "description": "List of structured weapon dictionaries to add or remove."
                },
                "reason": {
                    "type": "string",
                    "description": "In-world narrative reason for the inventory change (e.g. 'Found in dungeon chest', 'Consumed during battle')."
                }
            },
            "required": ["character_name", "action"]
        },
        handler=manage_party_inventory
    )

    # Register modular system adjudication tools (PF2e, CoC, Cyberpunk RED)
    register_modular_system_tools(dispatcher, service, emit_update)


def register_modular_system_tools(dispatcher, service: TTRPGService, notify_ui_callback=None):

    # 1. Pathfinder 2e Spell Adjudication
    async def cast_pathfinder_spell(
        character_name: str,
        spell_name: str,
        spell_rank: int,
        is_focus_spell: bool = False
    ) -> Dict[str, Any]:
        """Deducts a prepared/spontaneous spell slot or Focus Point for a Pathfinder 2e character."""
        char_id, char = service._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        native = char.get("native_system_data", {})
        casting = native.get("spellcasting", {})

        if is_focus_spell:
            focus = casting.get("focus_magic", {}).get("focus_points", {})
            current_fp = focus.get("current", 0)
            if current_fp <= 0:
                return {"status": "error", "message": f"{character_name} has 0 Focus Points remaining."}
            focus["current"] = current_fp - 1
            service.save_active_campaign_state()
            return {
                "status": "success",
                "message": f"{character_name} cast Focus Spell '{spell_name}'. Focus points: {focus['current']}/{focus['max']}."
            }

        slots = casting.get("spell_slots", {}).get(f"rank_{spell_rank}")
        if not slots:
            return {"status": "error", "message": f"Rank {spell_rank} slot not found."}

        if slots.get("current", 0) <= 0:
            return {"status": "error", "message": f"No Rank {spell_rank} spell slots remaining."}

        slots["current"] -= 1
        service.save_active_campaign_state()
        return {
            "status": "success",
            "message": f"{character_name} cast '{spell_name}' (Rank {spell_rank}). Slots remaining: {slots['current']}/{slots['max']}."
        }

    # 2. Call of Cthulhu Occult Spell Adjudication
    async def cast_coc_spell(
        character_name: str,
        spell_name: str,
        mp_cost: int,
        san_loss_formula: str = "1d4"
    ) -> Dict[str, Any]:
        """Casts a Call of Cthulhu ritual, deducting Magic Points and rolling Sanity loss."""
        char_id, char = service._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Investigator '{character_name}' not found."}

        vitals = char.setdefault("vitals", {})
        mp_data = vitals.setdefault("magic_points", {"current": 10, "max": 10})
        cur_mp = mp_data.get("current", 0)

        if cur_mp < mp_cost:
            return {"status": "error", "message": f"Insufficient Magic Points! Needs {mp_cost} MP, has {cur_mp} MP."}

        mp_data["current"] = cur_mp - mp_cost

        roll_res = service.parse_and_roll(san_loss_formula)
        san_loss = roll_res["total"] if roll_res.get("status") == "success" else 1

        san_data = vitals.setdefault("sanity", {"current": 50, "max": 99})
        old_san = san_data.get("current", 50)
        new_san = max(0, old_san - san_loss)
        san_data["current"] = new_san

        service.save_active_campaign_state()
        return {
            "status": "success",
            "spell_name": spell_name,
            "mp_spent": mp_cost,
            "mp_remaining": mp_data["current"],
            "sanity_lost": san_loss,
            "new_sanity": new_san,
            "summary": f"{character_name} completes the rite '{spell_name}'. Spent {mp_cost} MP, lost {san_loss} Sanity ({new_san} SAN remaining)."
        }

    # 3. Cyberpunk RED Damage & Ablation Adjudication
    async def apply_cyberpunk_damage(
        character_name: str,
        raw_damage: int,
        hit_location: str = "body"
    ) -> Dict[str, Any]:
        """
        Applies Cyberpunk RED damage: compares against Armor SP, subtracts absorbed damage,
        reduces HP on penetration, and ablates Armor SP by 1.
        """
        char_id, char = service._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        vitals = char.setdefault("vitals", {})
        armor_dict = vitals.setdefault("armor", {})
        armor_data = armor_dict.setdefault(hit_location.lower(), {})
        sp = armor_data.get("sp_current", char.get("armor_class", 0))
        armor_data["sp_current"] = sp

        if raw_damage <= sp:
            return {
                "status": "absorbed",
                "character_name": character_name,
                "damage_dealt": 0,
                "armor_sp": sp,
                "message": f"The armor absorbs the entire attack! (Damage {raw_damage} <= SP {sp})."
            }

        penetrating_damage = raw_damage - sp
        armor_data["sp_current"] = max(0, sp - 1)

        hp_entry = vitals.setdefault("health", {
            "current": char.get("current_health", 35),
            "max": char.get("max_health", 35)
        })
        hp_entry["current"] = max(0, hp_entry["current"] - penetrating_damage)

        is_seriously_wounded = hp_entry["current"] <= hp_entry.get("max", 35) // 2
        if is_seriously_wounded and "Seriously Wounded (-2 to Actions)" not in char.get("conditions", []):
            char.setdefault("conditions", []).append("Seriously Wounded (-2 to Actions)")

        service.save_active_campaign_state()

        return {
            "status": "penetrated",
            "character_name": character_name,
            "damage_dealt": penetrating_damage,
            "remaining_hp": hp_entry["current"],
            "new_sp": armor_data["sp_current"],
            "seriously_wounded": is_seriously_wounded,
            "summary": f"Attack penetrated {character_name}'s {hit_location} armor! Dealt {penetrating_damage} damage ({hp_entry['current']} HP left). Armor ablated to SP {armor_data['sp_current']}."
        }

    # Tool Registrations
    dispatcher.register_tool(
        name="cast_pathfinder_spell",
        description="Deducts a prepared/spontaneous spell slot or Focus Point for a Pathfinder 2e character.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "Casting character."},
                "spell_name": {"type": "string", "description": "Spell name."},
                "spell_rank": {"type": "integer", "description": "Spell rank level (1-10)."},
                "is_focus_spell": {"type": "boolean", "description": "True if using focus pool."}
            },
            "required": ["character_name", "spell_name", "spell_rank"]
        },
        handler=cast_pathfinder_spell
    )

    dispatcher.register_tool(
        name="cast_coc_spell",
        description="Casts a Call of Cthulhu ritual, deducting Magic Points and applying Sanity loss.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "Investigator name."},
                "spell_name": {"type": "string", "description": "Ritual name."},
                "mp_cost": {"type": "integer", "description": "Magic points required."},
                "san_loss_formula": {"type": "string", "description": "Sanity loss dice formula (e.g. '1d4', '1d6')."}
            },
            "required": ["character_name", "spell_name", "mp_cost"]
        },
        handler=cast_coc_spell
    )

    dispatcher.register_tool(
        name="apply_cyberpunk_damage",
        description="Applies damage in Cyberpunk RED with Armor SP comparison, penetration, and 1-point armor ablation.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "Target character."},
                "raw_damage": {"type": "integer", "description": "Total damage rolled by attacker."},
                "hit_location": {"type": "string", "enum": ["body", "head", "shield"], "description": "Armor zone struck."}
            },
            "required": ["character_name", "raw_damage"]
        },
        handler=apply_cyberpunk_damage
    )

    register_dm_adjudication_tools(dispatcher, service, notify_ui_callback)
    register_progression_tools(dispatcher, service, notify_ui_callback)


def register_dm_adjudication_tools(dispatcher, service: TTRPGService, notify_ui_callback=None):
    """
    Registers runtime DM Adjudication Tools for deterministic mutation of campaign state
    and real-time IPC/UI event broadcasting.
    """
    def _notify(event: str, data: Any):
        if callable(notify_ui_callback):
            try:
                notify_ui_callback(event, data)
            except Exception:
                pass

    async def modify_vitals(
        character_name: str,
        stat_name: str,
        delta: int
    ) -> Dict[str, Any]:
        """
        Modifies character vitals: 'health' (HP), 'temp_hp', or 'guard'.
        Automatically clamps health between 0 and max.
        """
        char_id, char = service._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        vitals = char.setdefault("vitals", {})
        stat_key = stat_name.lower().strip()

        if stat_key in ("health", "hp"):
            hp_data = vitals.setdefault("health", {"current": 10, "max": 10})
            old_val = hp_data["current"]
            new_val = max(0, min(hp_data["max"], old_val + delta))
            hp_data["current"] = new_val

            # Auto-apply Unconscious condition at 0 HP
            conds = char.setdefault("conditions", [])
            if new_val == 0 and "Unconscious" not in conds:
                conds.append("Unconscious")
            elif new_val > 0 and "Unconscious" in conds:
                conds.remove("Unconscious")

            service.save_active_campaign_state()
            _notify("roster_updated", service.get_campaign_roster())
            return {
                "status": "success",
                "character": character_name,
                "stat": "Health",
                "old": old_val,
                "current": new_val,
                "max": hp_data["max"],
                "summary": f"{character_name} HP adjusted by {delta:+d} ({old_val} -> {new_val}/{hp_data['max']})."
            }

        elif stat_key in ("temp_hp", "temphp", "temp"):
            vitals["temp_hp"] = max(0, vitals.get("temp_hp", 0) + delta)
            service.save_active_campaign_state()
            _notify("roster_updated", service.get_campaign_roster())
            return {"status": "success", "character": character_name, "temp_hp": vitals["temp_hp"]}

        return {"status": "error", "message": f"Unknown vitals field: '{stat_name}'."}

    async def cast_spell(
        character_name: str,
        spell_name: str,
        spell_level: int
    ) -> Dict[str, Any]:
        """
        Deducts an expendable spell slot when a spell is cast.
        Validates preparation state for leveled spells. Cantrips (Level 0) cost 0 slots.
        """
        char_id, char = service._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        native = char.get("native_system_data", {})
        spells = native.get("spells") or char.get("spells", [])
        slots = native.get("spell_slots") or char.get("spell_slots", {})

        # Find spell entry (matching with or without [R] tag)
        import re as _re
        clean_target = _re.sub(r'\s*\[r\]', '', spell_name, flags=_re.IGNORECASE).strip().lower()
        spell_entry = next(
            (
                s for s in spells
                if isinstance(s, dict) and (
                    s.get("name", "").lower() == spell_name.lower()
                    or _re.sub(r'\s*\[r\]', '', s.get("name", ""), flags=_re.IGNORECASE).strip().lower() == clean_target
                )
            ),
            None
        )

        class_str = str(native.get("class_level") or char.get("archetype") or char.get("class") or "").lower()
        is_wizard = "wizard" in class_str

        # Leveled Spell Verification
        if spell_level > 0:
            if spell_entry:
                is_ritual = bool(
                    spell_entry.get("is_ritual", False)
                    or "[r]" in str(spell_entry.get("name", "")).lower()
                    or "[r]" in spell_name.lower()
                )
                is_wizard_ritual = is_wizard and is_ritual
                if not spell_entry.get("prepared", False) and not is_wizard_ritual:
                    return {
                        "status": "error",
                        "message": f"Cannot cast '{spell_name}'! It is not currently prepared for today."
                    }

            slot_key = f"level_{spell_level}"
            if slot_key not in slots or slots[slot_key].get("current", 0) <= 0:
                return {
                    "status": "error",
                    "message": f"No Level {spell_level} spell slots remaining for {character_name}!"
                }

            slots[slot_key]["current"] -= 1

        service.save_active_campaign_state()
        _notify("roster_updated", service.get_campaign_roster())

        remaining = slots.get(f"level_{spell_level}", {}).get("current", "At-Will")
        return {
            "status": "success",
            "character": character_name,
            "spell": spell_name,
            "level": spell_level,
            "slots_remaining": remaining,
            "summary": f"{character_name} cast '{spell_name}' (Level {spell_level}). Slots remaining: {remaining}."
        }

    async def modify_resource_meter(
        character_name: str,
        meter_key: str,
        delta: int
    ) -> Dict[str, Any]:
        """
        Deducts or restores consumable pools: Sorcery Points, Draconic Cry, Healing Hands, Hit Dice.
        """
        char_id, char = service._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        meters = char.get("custom_meters", {})
        m_key = meter_key.lower().strip().replace(" ", "_")

        if m_key not in meters:
            return {"status": "error", "message": f"Meter '{meter_key}' not found on {character_name}."}

        meter = meters[m_key]
        old_val = meter.get("current", 0)
        new_val = max(0, min(meter.get("max", old_val), old_val + delta))
        meter["current"] = new_val

        service.save_active_campaign_state()
        _notify("roster_updated", service.get_campaign_roster())

        return {
            "status": "success",
            "character": character_name,
            "meter": meter.get("label", meter_key),
            "current": new_val,
            "max": meter.get("max", new_val),
            "summary": f"{character_name}'s {meter.get('label', meter_key)} adjusted by {delta:+d} ({old_val} -> {new_val}/{meter.get('max', new_val)})."
        }

    async def apply_condition(
        character_name: str,
        condition: str,
        active: bool = True
    ) -> Dict[str, Any]:
        """Applies or removes standard conditions (Poisoned, Blinded, Invisible, Prone)."""
        char_id, char = service._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        conds = char.setdefault("conditions", [])
        clean_cond = condition.strip().title()

        if active and clean_cond not in conds:
            conds.append(clean_cond)
        elif not active and clean_cond in conds:
            conds.remove(clean_cond)

        service.save_active_campaign_state()
        _notify("roster_updated", service.get_campaign_roster())

        return {
            "status": "success",
            "character": character_name,
            "conditions": conds,
            "summary": f"{clean_cond} {'applied to' if active else 'removed from'} {character_name}."
        }

    # Dispatcher Registrations
    dispatcher.register_tool(
        name="modify_vitals",
        description="Adjusts HP, Temp HP, or Guard.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string"},
                "stat_name": {"type": "string"},
                "delta": {"type": "integer"}
            },
            "required": ["character_name", "stat_name", "delta"]
        },
        handler=modify_vitals
    )
    dispatcher.register_tool(
        name="cast_spell",
        description="Deducts a spell slot and verifies preparation.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string"},
                "spell_name": {"type": "string"},
                "spell_level": {"type": "integer"}
            },
            "required": ["character_name", "spell_name", "spell_level"]
        },
        handler=cast_spell
    )
    dispatcher.register_tool(
        name="modify_resource_meter",
        description="Deducts/restores Sorcery Points or rest pools.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string"},
                "meter_key": {"type": "string"},
                "delta": {"type": "integer"}
            },
            "required": ["character_name", "meter_key", "delta"]
        },
        handler=modify_resource_meter
    )
    dispatcher.register_tool(
        name="apply_condition",
        description="Applies or clears conditions like Poisoned or Prone.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string"},
                "condition": {"type": "string"},
                "active": {"type": "boolean"}
            },
            "required": ["character_name", "condition"]
        },
        handler=apply_condition
    )
    register_enhanced_rest_tools(dispatcher, service, notify_ui_callback)


def register_enhanced_rest_tools(dispatcher, service: TTRPGService, notify_ui_callback=None):
    """
    Automated vs. Physical Dice Branching and Short Rest Recovery Engine.
    """
    def _notify(event: str, data: Any):
        if callable(notify_ui_callback):
            try:
                notify_ui_callback(event, data)
            except Exception:
                pass

    async def execute_rest(
        rest_type: str = "long",
        dice_mode: str = "digital"
    ) -> Dict[str, Any]:
        """
        Executes a Short or Long Rest across the entire campaign roster.
        - dice_mode: 'digital' (Aether auto-rolls Hit Dice healing) or 'physical' (prompts player to roll).
        """
        if not service.active_state:
            return {"status": "error", "message": "No active campaign state."}

        is_long = (rest_type.lower() == "long")
        chars = service.active_state.get("characters", {})
        prompts = []
        healed_reports = []

        for c_id, char in chars.items():
            name = char.get("name", c_id)
            vitals = char.setdefault("vitals", {})
            hp_data = vitals.setdefault("health", {"current": 10, "max": 10})
            native = char.get("native_system_data", {})
            meters = char.get("custom_meters") or native.get("custom_meters") or {}
            slots = native.get("spell_slots") or char.get("spell_slots", {})

            if is_long:
                # 1. Full HP Recovery
                hp_data["current"] = hp_data["max"]
                vitals["temp_hp"] = 0

                # 2. Reset All Spell Slots (Standard & Pact)
                for s_data in slots.values():
                    if isinstance(s_data, dict) and "max" in s_data:
                        s_data["current"] = s_data["max"]

                # 3. Recover 1/2 Max Hit Dice
                if "hit_dice" in meters:
                    hd = meters["hit_dice"]
                    recover_amount = max(1, hd["max"] // 2)
                    hd["current"] = min(hd["max"], hd["current"] + recover_amount)

                # 4. Reset All Long Rest & Short Rest Meters (Lay on Hands, Sorcery Points, etc.)
                for m_v in meters.values():
                    if isinstance(m_v, dict) and m_v.get("reset_on") in ("long_rest", "short_rest", "rest"):
                        m_v["current"] = m_v["max"]

                # 5. Clear Non-Attunement Conditions
                char["conditions"] = [c for c in char.get("conditions", []) if c == "Attuned"]

            else:
                # --- SHORT REST EXECUTION ---
                # 1. Recharge Warlock Pact Magic Slots
                is_pact = native.get("is_pact_magic", False)
                for s_key, s_data in slots.items():
                    if isinstance(s_data, dict) and (is_pact or s_data.get("is_pact_magic") or s_data.get("reset_on") == "short_rest"):
                        s_data["current"] = s_data["max"]

                # 2. Recharge Short Rest Consumables (Wild Shape, Channel Divinity, Ki, Second Wind)
                for m_v in meters.values():
                    if isinstance(m_v, dict) and m_v.get("reset_on") in ("short_rest", "rest"):
                        m_v["current"] = m_v["max"]

                # 3. Hit Dice Healing Evaluation
                current_hp = hp_data.get("current", 0)
                max_hp = hp_data.get("max", 10)
                hd_entry = meters.get("hit_dice")

                if current_hp < max_hp and hd_entry and hd_entry.get("current", 0) > 0:
                    con_val = native.get("ability_scores", {}).get("CON", 10)
                    con_mod = (con_val - 10) // 2
                    die_str = native.get("hit_dice", {}).get("die", "1d8")

                    if dice_mode == "digital":
                        spent_count = 0
                        total_healed = 0
                        while hp_data["current"] < max_hp and hd_entry["current"] > 0:
                            roll_res = service.parse_and_roll(die_str)
                            heal_amt = max(1, roll_res.get("total", 4) + con_mod)
                            hp_data["current"] = min(max_hp, hp_data["current"] + heal_amt)
                            hd_entry["current"] -= 1
                            spent_count += 1
                            total_healed += heal_amt

                        healed_reports.append(f"{name} spent {spent_count} Hit Dice (+{total_healed} HP, now {hp_data['current']}/{max_hp})")
                    else:
                        prompts.append({
                            "character": name,
                            "current_hp": current_hp,
                            "max_hp": max_hp,
                            "dice_remaining": hd_entry["current"],
                            "prompt": f"{name} is at {current_hp}/{max_hp} HP. Roll {die_str} + {con_mod} per die spent."
                        })

        service.save_active_campaign_state()
        _notify("roster_updated", service.get_campaign_roster())

        return {
            "status": "success",
            "rest_type": "Long Rest" if is_long else "Short Rest",
            "dice_mode": dice_mode,
            "automated_healing": healed_reports,
            "physical_prompts": prompts,
            "summary": (
                "Party completed a Long Rest: Full HP, spell slots, and abilities restored."
                if is_long else
                f"Short Rest completed. Pact slots, Wild Shape, and Channel Divinity recharged. {'; '.join(healed_reports)}"
            )
        }

    async def apply_physical_hit_die_roll(
        character_name: str,
        roll_total: int,
        dice_spent: int = 1
    ) -> Dict[str, Any]:
        """
        Receives reported physical dice rolls from players during short rests.
        Applies healing and decrements the character's Hit Dice pool.
        """
        char_id, char = service._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        vitals = char.setdefault("vitals", {})
        hp_data = vitals.setdefault("health", {"current": 10, "max": 10})
        hd_entry = char.setdefault("custom_meters", {}).setdefault("hit_dice", {"current": 1, "max": 1})

        if hd_entry["current"] < dice_spent:
            return {"status": "error", "message": f"{character_name} only has {hd_entry['current']} Hit Dice remaining."}

        hd_entry["current"] -= dice_spent
        old_hp = hp_data["current"]
        hp_data["current"] = min(hp_data["max"], old_hp + roll_total)

        service.save_active_campaign_state()
        _notify("roster_updated", service.get_campaign_roster())

        return {
            "status": "success",
            "character": character_name,
            "hp_restored": hp_data["current"] - old_hp,
            "new_hp": hp_data["current"],
            "max_hp": hp_data["max"],
            "hit_dice_remaining": hd_entry["current"],
            "summary": f"{character_name} healed for {roll_total} HP ({old_hp} -> {hp_data['current']}/{hp_data['max']}). Hit dice remaining: {hd_entry['current']}."
        }

    dispatcher.register_tool(
        name="execute_rest",
        description="Executes short or long rest with digital/physical dice support.",
        parameters={
            "type": "object",
            "properties": {
                "rest_type": {"type": "string", "enum": ["short", "long"]},
                "dice_mode": {"type": "string", "enum": ["digital", "physical"]}
            }
        },
        handler=execute_rest
    )
    dispatcher.register_tool(
        name="apply_physical_hit_die_roll",
        description="Applies reported physical dice roll healing during a rest.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string"},
                "roll_total": {"type": "integer"},
                "dice_spent": {"type": "integer"}
            },
            "required": ["character_name", "roll_total"]
        },
        handler=apply_physical_hit_die_roll
    )


def register_progression_tools(dispatcher, service: TTRPGService, notify_ui_callback=None):
    """
    Agent tool for programmatic character level scaling.
    """
    def _notify(event: str, data: Any):
        if callable(notify_ui_callback):
            try:
                notify_ui_callback(event, data)
            except Exception:
                pass

    async def level_up_character(
        character_name: str,
        target_level: int
    ) -> Dict[str, Any]:
        """
        Advances a character to target_level (1-20), recomputing Max HP, Hit Dice,
        Proficiency Bonus, 1st-9th level Spell Slots, and Prepared Spell Caps.
        """
        char_id, char = service._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        res = level_up_dnd_character(char, target_level)
        if res.get("status") == "success":
            service.save_active_campaign_state()
            _notify("roster_updated", service.get_campaign_roster())
            return {
                "status": "success",
                "character": character_name,
                "new_level": target_level,
                "max_hp": res["max_hp"],
                "proficiency_bonus": res["proficiency_bonus"],
                "save_dc": res["save_dc"],
                "max_prepared_spells": res["max_prepared_spells"],
                "summary": f"{character_name} advanced to Level {target_level}! HP: {res['max_hp']}, Save DC: {res['save_dc']}, Slots & Prepared Spells recomputed."
            }

        return res

    dispatcher.register_tool(
        name="level_up_character",
        description="Advances character level (1-20), updating HP, slots (1st-9th), save DC, and prepared spell cap.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "Character name."},
                "target_level": {"type": "integer", "description": "New target level (1-20)."}
            },
            "required": ["character_name", "target_level"]
        },
        handler=level_up_character
    )
