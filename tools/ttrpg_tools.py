"""
tools/ttrpg_tools.py - Agent Tools for Tabletop Adjudication & DM Document Grounding
Exposes mathematical dice rolling, vitals modification, turn ladder control,
campaign document review (rulesets, modules, character sheets), and roster character sync.
"""
from typing import Dict, Any, Optional
from core.ttrpg_service import TTRPGService


def register_ttrpg_tools(dispatcher, service: TTRPGService):

    def emit_update(event_name: str, payload: dict):
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
        Reads the extracted text of an uploaded campaign document (ruleset, campaign adventure,
        or character sheet) so Aether can review it and act as the Dungeon Master / Game Master.
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
        Use this after reviewing an uploaded character sheet document or when a player describes their character.
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
