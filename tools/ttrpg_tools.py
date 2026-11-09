"""
tools/ttrpg_tools.py - Agent Tools for Tabletop Adjudication & DM Document Grounding
Exposes mathematical dice rolling, vitals modification, turn ladder control,
campaign document review (rulesets, modules, character sheets), and roster character sync.
"""
import re
from typing import Dict, Any, List, Optional
from core.ttrpg_service import TTRPGService, level_up_dnd_character, CONDITION_EFFECTS, LONG_RANGE_MAGIC_EXCEPTIONS


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

    async def roll_dice(
        formula: str,
        roll_type: str = "normal",
        purpose: Optional[str] = None,
        character_name: Optional[str] = None,
        actor: Optional[str] = None,
        target_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes a cryptographically fair dice roll.
        Formula must be in standard notation like '1d20+5', '2d6', or '8d6+2'.
        roll_type must be one of: 'normal', 'advantage', 'disadvantage'.
        character_name / actor: The name of the character from the party roster, or monster/NPC/DM name.
        target_name: Optional target for proximity validation and target-based modifiers.
        """
        target_char = (character_name or actor or "").strip() or None
        state = getattr(service, "active_state", {}) or {}
        chars = state.get("characters", {}) if isinstance(state, dict) else {}
        if not target_char and purpose:
            for cid, cdata in chars.items():
                cname = (cdata.get("name") if isinstance(cdata, dict) else str(cdata)).strip()
                if cname and re.search(r'\b' + re.escape(cname) + r'\b', purpose, re.IGNORECASE):
                    target_char = cname
                    break
        if not target_char:
            caller_ref = state.get("party_caller_id") or state.get("party_caller")
            if caller_ref:
                if isinstance(chars, dict) and caller_ref in chars:
                    c = chars[caller_ref]
                    target_char = c.get("name") if isinstance(c, dict) else str(c)
                elif isinstance(chars, dict):
                    for cdata in chars.values():
                        cname = cdata.get("name") if isinstance(cdata, dict) else str(cdata)
                        if cname and cname.lower() == str(caller_ref).lower():
                            target_char = cname
                            break
                if not target_char:
                    target_char = str(caller_ref)
            if not target_char:
                ladder = state.get("initiative_order", [])
                turn_idx = state.get("active_turn_index", 0)
                if ladder and 0 <= turn_idx < len(ladder):
                    target_char = ladder[turn_idx].get("name")
            if not target_char and chars and len(chars) == 1:
                single_char = next(iter(chars.values()))
                target_char = single_char.get("name") if isinstance(single_char, dict) else str(single_char)
            if not target_char:
                target_char = "DM"

        p_low = (purpose or "").lower()
        is_death_save = "death save" in p_low or "death saving" in p_low or (formula.strip() == "1d20" and "death" in p_low)
        char_id, cdata = service._find_character(target_char) if target_char and target_char != "DM" else (None, None)

        # 1. Incapacitation check on actor (cannot take actions if incapacitated, unless rolling death save)
        if cdata and not is_death_save:
            conds = cdata.get("conditions", [])
            for cond in conds:
                c_base = cond.split("(")[0].strip()
                eff = CONDITION_EFFECTS.get(c_base, {})
                if eff.get("incapacitated"):
                    is_forced_save = ("saving throw" in p_low or "save vs" in p_low) and not ("attack" in p_low or "check" in p_low)
                    if not is_forced_save:
                        return {
                            "status": "error",
                            "message": f"Action blocked: {target_char} is currently {cond} and incapacitated."
                        }

        # 2. Proximity check for attacks/actions vs target
        if target_name and target_char and target_char != "DM":
            prox = service.check_entity_proximity(target_char, target_name, action_or_spell=purpose or "attack")
            if not prox.get("allowed", True):
                return {
                    "status": "error",
                    "message": prox.get("reason", f"{target_char} cannot target {target_name} due to spatial separation.")
                }

        # 3. Dynamic Advantage / Disadvantage adjustments from active conditions
        effective_roll_type = roll_type
        condition_notes = []
        if cdata:
            conds = cdata.get("conditions", [])
            is_attack = any(k in p_low for k in ("attack", "to hit", "strike", "slash", "shot", "swing", "fire"))
            is_check = any(k in p_low for k in ("check", "skill", "ability", "stealth", "perception", "athletics", "acrobatics", "investigation", "insight", "survival"))

            for cond in conds:
                c_base = cond.split("(")[0].strip()
                eff = CONDITION_EFFECTS.get(c_base, {})
                dis_rolls = eff.get("disadvantage_rolls", [])
                applies_dis = False
                if is_attack and "attack" in dis_rolls:
                    applies_dis = True
                elif is_check and "check" in dis_rolls:
                    dis_abils = eff.get("disadvantage_abilities", [])
                    if not dis_abils or any(ab in p_low for ab in dis_abils):
                        applies_dis = True
                if applies_dis:
                    condition_notes.append(f"{cond} (Disadvantage)")
                    if effective_roll_type == "normal":
                        effective_roll_type = "disadvantage"
                    elif effective_roll_type == "advantage":
                        effective_roll_type = "normal"

        if target_name and any(k in p_low for k in ("attack", "to hit", "strike")):
            _, t_data = service._find_character(target_name)
            t_comb = service._find_combatant(target_name) if not t_data else None
            t_conds = (t_data.get("conditions", []) if t_data else []) or (t_comb.get("conditions", []) if t_comb else [])
            for tc in t_conds:
                tc_base = tc.split("(")[0].strip()
                if tc_base in ("Blinded", "Paralyzed", "Stunned", "Unconscious", "Petrified"):
                    condition_notes.append(f"Target is {tc} (Advantage)")
                    if effective_roll_type == "normal":
                        effective_roll_type = "advantage"
                    elif effective_roll_type == "disadvantage":
                        effective_roll_type = "normal"

        res = service.parse_and_roll(formula, effective_roll_type, character_name=target_char)
        if res.get("status") == "success":
            if condition_notes:
                res["condition_adjustments"] = condition_notes
            if target_name:
                res["target_name"] = target_name
            if purpose:
                res["purpose"] = purpose
            if target_char:
                res["character_name"] = target_char

            # 4. Death Saving Throw Resolution
            if is_death_save and cdata:
                d20_roll = res.get("rolls", [10])[0] if res.get("rolls") else res.get("total", 10)
                ds = cdata.setdefault("death_saves", {"successes": 0, "failures": 0, "status": "dying"})
                char_conds = cdata.setdefault("conditions", [])
                msg = ""
                if d20_roll == 20:
                    service.modify_character_stat(target_char, "health", 1)
                    msg = f"NATURAL 20! {target_char} regains 1 HP, revives, and is no longer unconscious!"
                elif d20_roll == 1:
                    ds["failures"] = ds.get("failures", 0) + 2
                    if ds["failures"] >= 3:
                        ds["status"] = "dead"
                        if "Dead" not in char_conds:
                            char_conds.append("Dead")
                        msg = f"NATURAL 1! Critical failure! {target_char} suffers 2 death save failures ({ds['failures']}/3) and has DIED."
                    else:
                        msg = f"NATURAL 1! Critical failure! {target_char} suffers 2 death save failures ({ds['failures']}/3)."
                elif d20_roll >= 10:
                    ds["successes"] = ds.get("successes", 0) + 1
                    if ds["successes"] >= 3:
                        ds["status"] = "stable"
                        if "Stable" not in char_conds:
                            char_conds.append("Stable")
                        msg = f"SUCCESS (Roll: {d20_roll})! {target_char} records a death save success ({ds['successes']}/3). STABLE at 0 HP!"
                    else:
                        msg = f"SUCCESS (Roll: {d20_roll})! {target_char} records a death save success ({ds['successes']}/3)."
                else:
                    ds["failures"] = ds.get("failures", 0) + 1
                    if ds["failures"] >= 3:
                        ds["status"] = "dead"
                        if "Dead" not in char_conds:
                            char_conds.append("Dead")
                        msg = f"FAILURE (Roll: {d20_roll})! {target_char} suffers a death save failure ({ds['failures']}/3) and has DIED."
                    else:
                        msg = f"FAILURE (Roll: {d20_roll})! {target_char} suffers a death save failure ({ds['failures']}/3)."

                service.save_active_campaign_state()
                res["death_save_result"] = msg
                res["death_saves"] = ds
                res["dm_directive"] = f"DEATH SAVE RESOLUTION: {msg}"
                emit_update("roster_updated", service.get_campaign_roster())
                if service.active_state and service.active_state.get("initiative_order"):
                    emit_update("initiative_updated", {"initiative_order": service.active_state.get("initiative_order", [])})

            # Automatic Initiative Tracking Bridge:
            if "initiative" in p_low:
                total_val = res.get("total", 10)
                actor_name = target_char or character_name or "Unknown"
                service.clean_stale_combat_if_needed()
                char_id_init, cdata_init = service._find_character(actor_name)
                is_p = bool(cdata_init is not None)
                service.add_to_initiative(name=actor_name, roll_val=total_val, is_player=is_p)
                emit_update("initiative_updated", {"initiative_order": service.active_state.get("initiative_order", [])})
                emit_update("ttrpg_state_updated", {"initiative_order": service.active_state.get("initiative_order", [])})

            # Stored gear search check
            stored_list = state.get("stored_items", []) if isinstance(state, dict) else []
            if any(it.get("location_knowledge") in (None, "unknown") for it in stored_list):
                if any(k in p_low for k in ("perception", "investigation", "search", "look", "spot", "find", "weapon", "gear", "chest", "evidence")):
                    res["dm_directive"] = (
                        f"NOTE FOR GM: If this check ({res.get('total')}) spots or perceives where the party's confiscated gear is kept, "
                        "you MUST invoke `manage_party_inventory(action='discover_location', storage_location='<Location Seen>', discovery_clue='<Visual Clue>')` "
                        "immediately so the party roster UI updates from 'Location Unknown' to 'Location Known'!"
                    )
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
        Supports party heroes as well as monsters on the initiative ladder.
        """
        res = service.modify_character_stat(
            character_name=character_name,
            stat_key=stat_name.lower().strip(),
            delta=amount,
            is_custom_meter=is_custom_meter
        )
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
            if service.active_state:
                emit_update("initiative_updated", {"initiative_order": service.active_state.get("initiative_order", [])})
            emit_update("roster_updated", service.get_campaign_roster())
        return res

    async def advance_combat_turn() -> Dict[str, Any]:
        """
        Advances the initiative tracker to the next turn or round in active combat.
        Returns the active character who is currently taking their turn.
        """
        res = service.advance_turn()
        if res.get("status") == "success":
            emit_update("initiative_updated", res)
            emit_update("ttrpg_state_updated", res)
        return res

    async def start_combat(enemies: Optional[List[str]] = None, round_number: int = 1) -> Dict[str, Any]:
        """
        Begins a combat encounter: rolls initiative for all party heroes and enemies,
        populates the Initiative Ladder in the UI, and starts Round 1.
        """
        res = service.start_combat(enemies=enemies, round_number=round_number)
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
            emit_update("initiative_updated", res)
        return res

    async def end_combat() -> Dict[str, Any]:
        """Clears the initiative ladder and resets turn tracking when combat ends."""
        res = service.end_combat()
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
            emit_update("initiative_updated", res)
        return res

    async def add_to_initiative(name: str, roll_val: int, is_player: bool = False) -> Dict[str, Any]:
        """Adds or updates a single combatant on the initiative ladder."""
        res = service.add_to_initiative(name=name, roll_val=roll_val, is_player=is_player)
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
            emit_update("initiative_updated", res)
        return res

    async def remove_from_initiative(name_or_id: str) -> Dict[str, Any]:
        """Removes a combatant from the initiative ladder (e.g. killed, fled, surrendered)."""
        res = service.remove_from_initiative(name_or_id=name_or_id)
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
            emit_update("initiative_updated", res)
        return res

    async def clear_defeated_combatants() -> Dict[str, Any]:
        """Removes all dead/defeated enemies from the active initiative ladder."""
        res = service.clear_defeated_combatants()
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
            emit_update("initiative_updated", res)
        return res

    async def set_character_condition(character_name: str, condition: str, is_active: bool) -> Dict[str, Any]:
        """Applies or clears a status condition (e.g., Poisoned, Stunned, Fatigued, Stable, Dead) on a character."""
        res = service.update_condition(character_name, condition, is_active)
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
            emit_update("roster_updated", service.get_campaign_roster())
            if service.active_state and service.active_state.get("initiative_order"):
                emit_update("initiative_updated", {"initiative_order": service.active_state.get("initiative_order", [])})
        return res

    async def update_entity_location(
        name: str,
        location: str,
        is_separated: Optional[bool] = None,
        reunite_party: bool = False
    ) -> Dict[str, Any]:
        """
        Updates the spatial location of a character, NPC, or monster in the world.
        If reunite_party is True, brings all party characters to 'location' and clears separation.
        """
        res = service.update_entity_location(
            name=name,
            location=location,
            is_separated=is_separated,
            reunite_party=reunite_party
        )
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
            emit_update("roster_updated", service.get_campaign_roster())
        return res

    async def check_entity_proximity(
        actor_name: str,
        target_name: str,
        action_or_spell: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Checks if actor and target are co-located or within reach for actions/spells.
        """
        return service.check_entity_proximity(
            actor_name=actor_name,
            target_name=target_name,
            action_or_spell=action_or_spell
        )

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
        description="Rolls fair dice using standard notation (e.g., '1d20+5', '2d6') with support for advantage and disadvantage. Must always be associated with a player character from the party roster or monster/NPC/DM.",
        parameters={
            "type": "object",
            "properties": {
                "formula": {"type": "string", "description": "Dice expression (e.g., '1d20+7', '3d6')."},
                "roll_type": {"type": "string", "enum": ["normal", "advantage", "disadvantage"], "description": "Roll modifier."},
                "purpose": {"type": "string", "description": "Context of roll (e.g., 'Joe Investigation Check', 'Goblin Scimitar Attack', 'Fireball Damage', 'Death Saving Throw')."},
                "character_name": {"type": "string", "description": "Name of the character from the Party Roster performing the action (e.g. 'Joe', 'Sam', 'Shadow'), or NPC/Monster/DM name (e.g. 'Chief Olaf', 'Goblin', 'Game Master')."},
                "target_name": {"type": "string", "description": "Optional target creature, player, or NPC. Used to check spatial proximity and target-based modifiers."}
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
        name="start_combat",
        description="Begins a combat encounter. Automatically rolls initiative for all party heroes and listed enemies, populates the Initiative Ladder in the UI, and starts Round 1.",
        parameters={
            "type": "object",
            "properties": {
                "enemies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of enemy names in the encounter (e.g. ['Ape 1', 'Ape 2', 'Ape 3', 'Ape 4', 'Ape 5'])."
                },
                "round_number": {
                    "type": "integer",
                    "description": "Initial combat round number (defaults to 1)."
                }
            },
            "required": ["enemies"]
        },
        handler=start_combat
    )

    dispatcher.register_tool(
        name="end_combat",
        description="Ends combat encounter and clears the initiative ladder.",
        parameters={"type": "object", "properties": {}},
        handler=end_combat
    )

    dispatcher.register_tool(
        name="add_to_initiative",
        description="Adds a reinforcement or individual combatant to the initiative ladder.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Combatant name."},
                "roll_val": {"type": "integer", "description": "Initiative roll total."},
                "is_player": {"type": "boolean", "description": "True if player character, False if monster/NPC."}
            },
            "required": ["name", "roll_val"]
        },
        handler=add_to_initiative
    )

    dispatcher.register_tool(
        name="remove_from_initiative",
        description="Removes a combatant from the initiative ladder when defeated, killed, or fled.",
        parameters={
            "type": "object",
            "properties": {
                "name_or_id": {"type": "string", "description": "Name or entity ID to remove."}
            },
            "required": ["name_or_id"]
        },
        handler=remove_from_initiative
    )

    dispatcher.register_tool(
        name="clear_defeated_combatants",
        description="Removes all dead/defeated enemies from the active initiative tracker when combat concludes.",
        parameters={"type": "object", "properties": {}},
        handler=clear_defeated_combatants
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
        name="update_entity_location",
        description="Updates the spatial location of a character, NPC, or monster in the active campaign. Use when party members separate into different areas or reunite.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Character or NPC/monster name, or 'all' to target the entire party."},
                "location": {"type": "string", "description": "The specific area or location name (e.g. 'Hedge Maze - Overgrown Heart', 'Camp Jail Cell')."},
                "is_separated": {"type": "boolean", "description": "Explicitly mark whether this character is separated from the main party."},
                "reunite_party": {"type": "boolean", "description": "Set to True to bring all party members together at this location."}
            },
            "required": ["name", "location"]
        },
        handler=update_entity_location
    )

    dispatcher.register_tool(
        name="check_entity_proximity",
        description="Checks if an actor is close enough to target another character/creature with an action or spell, preventing actions across separated areas.",
        parameters={
            "type": "object",
            "properties": {
                "actor_name": {"type": "string", "description": "The character or creature performing the action."},
                "target_name": {"type": "string", "description": "The intended target of the action or spell."},
                "action_or_spell": {"type": "string", "description": "The spell or action name (e.g. 'Command', 'Fireball', 'Sending')."}
            },
            "required": ["actor_name", "target_name"]
        },
        handler=check_entity_proximity
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
        reason: Optional[str] = None,
        storage_location: Optional[str] = None,
        location_knowledge: Optional[str] = None,
        status: Optional[str] = None,
        holder_or_creature: Optional[str] = None,
        salvageable: Optional[bool] = None,
        discovery_clue: Optional[str] = None
    ) -> Dict[str, Any]:
        res = service.manage_character_inventory(
            character_name=character_name,
            action=action,
            items=items,
            weapons=weapons,
            reason=reason,
            storage_location=storage_location,
            location_knowledge=location_knowledge,
            status=status,
            holder_or_creature=holder_or_creature,
            salvageable=salvageable,
            discovery_clue=discovery_clue
        )
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
        return res

    dispatcher.register_tool(
        name="manage_party_inventory",
        description="Adds, removes, uses, disarms, retrieves, discovers, or destroys physical items and weapons for a party member or the entire party. Supports 'disarm' upon arrest/surrender (storing weapons in evidence with unknown or known location), 'discover_location' (revealing storage location upon successful checks/clues), 'retrieve' (reclaiming stored gear or salvaging slayed beasts), and 'destroy' (permanent incineration in lava/acid where items are lost forever). NOTE: Captives, prisoners, and living NPCs are NOT inventory!",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {
                    "type": "string",
                    "description": "Name of the character (e.g. 'Joe', 'Shadow'), or 'all'/'party' to disarm, discover, or retrieve gear for the entire party at once."
                },
                "action": {
                    "type": "string",
                    "enum": ["add", "remove", "use", "disarm", "retrieve", "discover_location", "destroy"],
                    "description": "Action type: 'add' (loot/receive), 'remove' (discard/swallowed by creature), 'use' (potions/scrolls), 'disarm' (strip weapons upon arrest/surrender and store in evidence), 'retrieve' (reclaim stored/confiscated weapons/items from evidence or salvage slayed beast), 'discover_location' (reveal where gear is kept after checks/clues), or 'destroy' (incinerate permanently in lava/acid)."
                },
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of item names to add, remove, retrieve, or destroy (e.g. ['Potion of Healing', '50 ft. Rope', 'Gold pouch'])."
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
                "storage_location": {
                    "type": "string",
                    "description": "Physical in-world location where removed/confiscated items are placed, retrieved from, or destroyed in (e.g. 'Camp Highmore Guard Barracks - Evidence Chest', 'Jailor's Office Strongbox', 'Mount Doom Lava'). Ensures item persistence so players can reclaim gear later."
                },
                "location_knowledge": {
                    "type": "string",
                    "enum": ["unknown", "known", "discovered"],
                    "description": "Information asymmetry flag. 'unknown': guards took gear away unseen by players (displayed as 'Confiscated Gear (Location Unknown)'); 'known': witnessed by players (e.g. dragon swallowed it in front of them); 'discovered': learned via Perception check, interrogation, or finding ledgers."
                },
                "status": {
                    "type": "string",
                    "enum": ["confiscated", "inside_creature", "destroyed", "stored", "lost"],
                    "description": "Status of the stored items. 'confiscated' (by authorities), 'inside_creature' (swallowed by monster, salvageable upon slaying), 'destroyed' (incinerated/vaporized, permanent loss), 'stored' or 'lost'."
                },
                "holder_or_creature": {
                    "type": "string",
                    "description": "Name of the beast, monster, or NPC holding or having swallowed the item (e.g. 'Red Dragon', 'Beholder', 'Captain Valen')."
                },
                "salvageable": {
                    "type": "boolean",
                    "description": "Whether the item can be recovered. True for beasts (salvageable upon slaying), False for items incinerated in lava or vaporized."
                },
                "discovery_clue": {
                    "type": "string",
                    "description": "Narrative explanation or check result explaining how players learned where gear is kept (e.g. 'DC 13 Perception check overhearing guards in corridor', 'Found armory ledger on desk')."
                },
                "reason": {
                    "type": "string",
                    "description": "In-world narrative reason for the inventory change (e.g. 'Arrested by Chief Filip', 'Swallowed by Red Dragon', 'Cast into Mount Doom lava', 'Found in dungeon chest')."
                }
            },
            "required": ["character_name", "action"]
        },
        handler=manage_party_inventory
    )

    async def lookup_dnd_compendium(
        query_or_name: str = "",
        category: str = "all",
        edition: str = "2024",
        class_name: Optional[str] = None,
        max_level: Optional[int] = None,
        expand_pack: bool = True,
    ) -> Dict[str, Any]:
        """
        Searches the offline D&D 5e (2014 SRD 5.1 & 2024 SRD 5.2) + Custom Homebrew compendium
        for weapons, 2024 weapon masteries, armor/shields, adventuring gear/packs/magic items,
        and class spells with rules, damage, cost, weight, and 2014 vs 2024 differences.
        """
        cat_clean = (category or "all").strip().lower()
        q_clean = (query_or_name or "").strip()
        ed_clean = (edition or "2024").strip().lower()

        if cat_clean in ("masteries", "mastery", "weapon_masteries"):
            from core.compendiums.dnd_weapons import list_weapon_masteries
            masteries = list_weapon_masteries()
            if q_clean:
                ql = q_clean.lower()
                masteries = [m for m in masteries if ql in m.get("name", "").lower() or ql in m.get("description", "").lower()]
            return {
                "status": "success",
                "category": "masteries",
                "edition": "2024",
                "count": len(masteries),
                "results": masteries,
                "summary": ", ".join(f"{m['name']}: {m['description']}" for m in masteries[:8]),
            }

        from core.compendiums import search_compendium
        from core.compendiums.dnd_items import expand_pack_contents

        filters: Dict[str, Any] = {}
        if class_name:
            filters["class_name"] = class_name
        if max_level is not None:
            filters["max_level"] = int(max_level)

        matches = search_compendium(
            category=cat_clean,
            query=q_clean,
            edition=ed_clean,
            filters=filters or None,
            limit=12,
        )

        compact_results = []
        for m in matches:
            ctype = m.get("compendium_type", cat_clean)
            entry: Dict[str, Any] = {
                "name": m.get("name"),
                "index": m.get("index"),
                "category": ctype,
                "editions": m.get("editions", []),
                "is_homebrew": bool(m.get("is_homebrew", False)),
            }
            if ctype == "weapons":
                entry.update({
                    "weapon_category": m.get("category"),
                    "damage": m.get("damage"),
                    "properties": m.get("properties", []),
                    "mastery": m.get("mastery", ""),
                    "range": m.get("range", ""),
                    "cost": m.get("cost_display", ""),
                    "weight": m.get("weight", 0),
                })
                if m.get("description"):
                    entry["description"] = str(m["description"])[:320]
            elif ctype == "armor":
                entry.update({
                    "armor_category": m.get("category"),
                    "ac_base": m.get("ac_base"),
                    "dex_bonus": m.get("dex_bonus"),
                    "max_dex_bonus": m.get("max_dex_bonus"),
                    "str_minimum": m.get("str_minimum"),
                    "stealth_disadvantage": m.get("stealth_disadvantage"),
                    "cost": m.get("cost_display", ""),
                    "weight": m.get("weight", 0),
                })
            elif ctype == "items":
                entry.update({
                    "subcategory": m.get("subcategory") or m.get("category"),
                    "cost": m.get("cost_display", ""),
                    "cost_cp": m.get("cost_cp", 0),
                    "weight": m.get("weight", 0),
                    "is_magic": bool(m.get("is_magic", False)),
                    "rarity": m.get("rarity", "Common"),
                    "requires_attunement": bool(m.get("requires_attunement", False)),
                    "description": str(m.get("description") or "")[:360],
                })
                if expand_pack and m.get("contents"):
                    entry["pack_contents"] = expand_pack_contents(m.get("index") or m.get("name"))
            elif ctype == "spells":
                desc = (
                    m.get("description_2024")
                    if ed_clean == "2024" and m.get("description_2024")
                    else (m.get("description_2014") if ed_clean == "2014" and m.get("description_2014") else m.get("description", ""))
                )
                entry.update({
                    "level": m.get("level", 0),
                    "level_label": m.get("level_label", ""),
                    "school": m.get("school", ""),
                    "classes": m.get("classes", []),
                    "casting_time": m.get("casting_time", ""),
                    "range": m.get("range", ""),
                    "components": m.get("components", []),
                    "material": m.get("material", ""),
                    "duration": m.get("duration", ""),
                    "concentration": bool(m.get("concentration", False)),
                    "ritual": bool(m.get("ritual", False)),
                    "damage": m.get("damage", ""),
                    "dc_type": m.get("dc_type", ""),
                    "description": str(desc or "")[:450],
                })
                if m.get("notes_2024"):
                    entry["notes_2024"] = m["notes_2024"]
            compact_results.append(entry)

        summary_names = ", ".join(r["name"] for r in compact_results[:6] if r.get("name"))
        return {
            "status": "success",
            "category": cat_clean,
            "edition": ed_clean,
            "count": len(compact_results),
            "results": compact_results,
            "summary": f"Found {len(compact_results)} D&D 5e ({ed_clean}) compendium match(es): {summary_names}" if compact_results else f"No D&D 5e compendium matches found for '{q_clean}' in '{cat_clean}'.",
        }

    dispatcher.register_tool(
        name="lookup_dnd_compendium",
        description="Looks up official D&D 5e (2014 & 2024 SRD) and Homebrew weapons, 2024 weapon masteries, armor/shields, equipment/magic items, equipment packs, and class spells with stats, prices, and descriptions.",
        parameters={
            "type": "object",
            "properties": {
                "query_or_name": {"type": "string", "description": "Item, weapon, armor, spell, mastery name, or search keyword (empty to list by filters)."},
                "category": {"type": "string", "enum": ["all", "weapons", "armor", "items", "spells", "masteries"], "description": "Compendium table to query."},
                "edition": {"type": "string", "enum": ["2024", "2014", "all"], "description": "D&D 5e rules edition filter (defaults to 2024)."},
                "class_name": {"type": "string", "description": "Optional spellcaster class filter when category='spells' (e.g. 'Cleric', 'Wizard', 'Paladin')."},
                "max_level": {"type": "integer", "description": "Optional max spell level filter (0-9) when category='spells'."},
                "expand_pack": {"type": "boolean", "description": "If true, expands Equipment Pack contents (e.g. Explorer's Pack)."}
            }
        },
        handler=lookup_dnd_compendium
    )

    async def open_merchant_shop(
        shop_name: str = "Merchant's Wares",
        location_name: str = "",
        price_multiplier: float = 1.0,
        specialty: str = "general",
        description: str = "",
        include_magic: bool = False,
    ) -> Dict[str, Any]:
        """Opens the interactive Merchant Shop modal in the UI with regional markup/discount pricing."""
        res = service.set_active_shop(
            shop_name=shop_name,
            location_name=location_name,
            price_multiplier=price_multiplier,
            specialty=specialty,
            description=description,
            include_magic=include_magic,
        )
        if res.get("status") == "success":
            emit_update("shop_opened", res["shop"])
            emit_update("ttrpg_state_updated", res)
        return res

    async def merchant_transaction(
        character_name: str,
        action: str = "buy",
        item_name: str = "",
        category: str = "auto",
        qty: int = 1,
        price_multiplier: float = 1.0,
        sell_rate: float = 0.5,
        equip: bool = False,
    ) -> Dict[str, Any]:
        """Executes a merchant buy or sell transaction with multi-coin currency conversion and inventory sync."""
        act = (action or "buy").strip().lower()
        if act in ("sell", "trade_in"):
            res = service.sell_merchant_item(
                character_name=character_name,
                item_name=item_name,
                sell_rate=sell_rate,
                qty=qty,
                source=category if category in ("inventory", "weapons") else "auto",
            )
        else:
            res = service.purchase_merchant_item(
                character_name=character_name,
                category=category,
                index_or_name=item_name,
                qty=qty,
                price_multiplier=price_multiplier,
                expand_pack=True,
                equip=equip,
            )
        if res.get("status") == "success":
            emit_update("ttrpg_state_updated", res)
            emit_update("roster_updated", service.get_campaign_roster())
        return res

    dispatcher.register_tool(
        name="open_merchant_shop",
        description="Opens or updates the interactive Merchant Shop UI popup with story-driven markup/discount pricing (e.g. 0.8x allied quartermaster, 1.0x village, 1.25x-1.35x frontier outpost/scarcity, 1.5x black market). Call this tool whenever the party enters a shop or when haggling/story events change merchant prices.",
        parameters={
            "type": "object",
            "properties": {
                "shop_name": {"type": "string", "description": "Shop or merchant NPC name (e.g. 'Tane Here\'s Trade Post', 'Ironforge Smithy')."},
                "location_name": {"type": "string", "description": "World location or district (e.g. 'Camp Highmore Outpost', 'Waterdeep Trade Ward')."},
                "price_multiplier": {"type": "number", "description": "Price multiplier from 0.5 (50% discount) to 2.0 (2x markup) based on location, scarcity, or haggling. Default 1.0."},
                "specialty": {"type": "string", "enum": ["general", "weapons_armor", "smith", "potions", "alchemist", "arcane", "magic_poison", "gear"], "description": "Merchant stock specialization."},
                "description": {"type": "string", "description": "Short shopkeeper flavor note or haggling response shown at top of the shop."},
                "include_magic": {"type": "boolean", "description": "Whether magic items are stocked."}
            },
            "required": ["shop_name"]
        },
        handler=open_merchant_shop
    )

    dispatcher.register_tool(
        name="merchant_transaction",
        description="Buys or sells an SRD/Homebrew weapon, armor, potion, pack, or gear item for a character using their D&D coin purse (pp/gp/ep/sp/cp) with automatic change and inventory updates.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "Character paying or receiving coins."},
                "action": {"type": "string", "enum": ["buy", "sell"], "description": "'buy' to purchase or 'sell' to sell owned gear."},
                "item_name": {"type": "string", "description": "Item, weapon, armor, or pack name (e.g. 'Rations (1 day)', 'Longsword', 'Explorer\'s Pack')."},
                "category": {"type": "string", "enum": ["auto", "weapons", "armor", "items", "inventory"], "description": "Item category (default 'auto')."},
                "qty": {"type": "integer", "description": "Quantity to buy or sell (default 1)."},
                "price_multiplier": {"type": "number", "description": "Merchant markup/discount when buying (0.5 to 2.0, default 1.0)."},
                "sell_rate": {"type": "number", "description": "Fraction of base value paid when selling (default 0.5)."},
                "equip": {"type": "boolean", "description": "If true when buying armor/shield, equips immediately and updates AC."}
            },
            "required": ["character_name", "item_name"]
        },
        handler=merchant_transaction
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
        Supports both party characters and active combatants on the initiative ladder.
        """
        stat_key = (stat_name or "health").lower().strip()
        if stat_key in ("health", "hp"):
            res = service.modify_character_stat(
                character_name=character_name,
                stat_key="health",
                delta=delta
            )
            if res.get("status") == "success":
                _notify("ttrpg_state_updated", res)
                if service.active_state:
                    _notify("initiative_updated", {"initiative_order": service.active_state.get("initiative_order", [])})
                _notify("roster_updated", service.get_campaign_roster())
                return {
                    "status": "success",
                    "character": res.get("character_name", character_name),
                    "stat": "Health",
                    "old": res.get("old_value"),
                    "current": res.get("new_value"),
                    "max": res.get("max_value"),
                    "health_status": res.get("health_status"),
                    "summary": res.get("summary", f"{character_name} HP adjusted by {delta:+d}.")
                }
            return res

        elif stat_key in ("guard", "ac", "defense", "armor_class"):
            res = service.modify_character_stat(
                character_name=character_name,
                stat_key="guard",
                delta=delta
            )
            if res.get("status") == "success":
                _notify("ttrpg_state_updated", res)
                if service.active_state:
                    _notify("initiative_updated", {"initiative_order": service.active_state.get("initiative_order", [])})
                return res
            return res

        char_id, char = service._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        vitals = char.setdefault("vitals", {})
        if stat_key in ("temp_hp", "temphp", "temp"):
            vitals["temp_hp"] = max(0, vitals.get("temp_hp", 0) + delta)
            service.save_active_campaign_state()
            _notify("roster_updated", service.get_campaign_roster())
            return {"status": "success", "character": character_name, "temp_hp": vitals["temp_hp"]}

        return {"status": "error", "message": f"Unknown vitals field: '{stat_name}'."}

    async def cast_spell(
        character_name: str,
        spell_name: str,
        spell_level: int,
        slot_level: Optional[int] = None,
        target_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Deducts an expendable spell slot when a spell is cast.
        Validates preparation state for leveled spells. Cantrips (Level 0) cost 0 slots.
        Supports upcasting, Warlock Pact Magic, condition restrictions, and target proximity.
        """
        res = service.cast_character_spell(
            character_name=character_name,
            spell_name=spell_name,
            spell_level=spell_level,
            slot_level=slot_level,
            target_name=target_name
        )
        if res.get("status") == "success":
            _notify("ttrpg_state_updated", res)
            _notify("roster_updated", service.get_campaign_roster())
        return res

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
        description="Deducts a spell slot and verifies preparation. Supports upcasting and Warlock Pact Magic (expending higher-level slots). Cantrips cost 0 slots.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "Name of the caster from the party roster (e.g. 'Shadow', 'Sam', 'Gale')."},
                "spell_name": {"type": "string", "description": "Name of the spell to cast (e.g. 'Dimension Door', 'Misty Step', 'Fireball')."},
                "spell_level": {"type": "integer", "description": "Base spell level (0 for Cantrips, 1-9 for leveled spells)."},
                "slot_level": {"type": "integer", "description": "Optional slot level to expend when upcasting (e.g. 5 for Warlock Pact slot). Defaults to lowest available slot >= spell_level."},
                "target_name": {"type": "string", "description": "Optional target creature, player, or NPC for the spell. Validates proximity and charm restrictions."}
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

                # 4. Reset All Long Rest & Short Rest Meters (Lay on Hands, Sorcery Points, etc. — preserve hit_dice half-recovery)
                for m_k, m_v in meters.items():
                    if m_k != "hit_dice" and isinstance(m_v, dict) and m_v.get("reset_on") in ("long_rest", "short_rest", "rest"):
                        m_v["current"] = m_v["max"]

                # 5. Clear Non-Attunement Conditions
                char["conditions"] = [c for c in char.get("conditions", []) if c == "Attuned"]

            else:
                # --- SHORT REST EXECUTION ---
                # 1. Recharge Warlock Pact Magic Slots (without refilling Long Rest Spellcasting slots on multiclass casters)
                is_pact = bool(native.get("is_pact_magic", False))
                has_long_slots = any(
                    isinstance(sv, dict) and sv.get("reset_on") == "long_rest" and int(sv.get("max") or 0) > 0
                    for sv in slots.values()
                )
                for s_key, s_data in slots.items():
                    if isinstance(s_data, dict):
                        if s_data.get("pact_max") is not None:
                            s_data["pact_current"] = s_data["pact_max"]
                        if s_data.get("is_pact_magic") or s_data.get("reset_on") == "short_rest" or (is_pact and not has_long_slots and s_data.get("reset_on") != "long_rest"):
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

    async def prompt_party_rest(
        rest_type: str = "short",
        reason: str = "",
    ) -> Dict[str, Any]:
        """Opens the interactive Rest modal popup for players to spend Hit Dice, drink potions, swap armor/attunement, and prepare spells."""
        rt = "long" if str(rest_type or "").lower().strip() == "long" else "short"
        ed = service._get_campaign_edition() if hasattr(service, "_get_campaign_edition") else "2024"
        payload = {
            "status": "success",
            "rest_type": rt,
            "reason": (reason or "").strip(),
            "dice_mode": service.get_dice_mode() if hasattr(service, "get_dice_mode") else "digital",
            "edition": ed,
        }
        _notify("rest_prompt_opened", payload)
        return {
            **payload,
            "message": f"Opened the interactive {'Long Rest' if rt == 'long' else 'Short Rest'} window for the party so players can manage Hit Dice, consumables, equipment, attunement, and prepared spells.",
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
        name="prompt_party_rest",
        description="Opens the interactive Short/Long Rest popup UI for players to spend Hit Dice, consume potions/rations, swap armor/shield/attuned magic items (max 3), and re-prepare class spells.",
        parameters={
            "type": "object",
            "properties": {
                "rest_type": {"type": "string", "enum": ["short", "long"], "description": "Whether the party is taking a Short Rest (1 hour) or Long Rest (8 hours)."},
                "reason": {"type": "string", "description": "Optional narrative context (e.g. 'Making camp in the Feywild grove')."}
            },
            "required": ["rest_type"]
        },
        handler=prompt_party_rest
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

    async def prompt_level_up_wizard(
        character_name: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Opens the interactive D&D 5e (2014 & 2024) Guided Level-Up & Multiclass Wizard popup for a character."""
        char_id, char = service._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Character '{character_name}' not found."}

        preview = (
            service.get_character_advancement_options(character_name)
            if hasattr(service, "get_character_advancement_options")
            else {"status": "error", "message": "Advancement options unavailable."}
        )
        ed = preview.get("edition") or (service._get_campaign_edition() if hasattr(service, "_get_campaign_edition") else "2024")
        payload = {
            "status": "success",
            "source": "campaign",
            "character_id": char_id,
            "character_name": char.get("name", character_name),
            "edition": ed,
            "reason": (reason or "").strip(),
            "preview": preview,
        }
        _notify("level_up_prompt_opened", payload)
        return {
            **payload,
            "message": (
                f"Opened the Guided Level-Up & Multiclass Wizard for {char.get('name', character_name)} ({ed} rules) "
                f"so the player can choose class advancement or multiclassing, HP roll/average, ASI/Feat, Subclass, and new spells."
            ),
        }

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
    dispatcher.register_tool(
        name="prompt_level_up_wizard",
        description="Opens the interactive D&D 5e (2014 & 2024) Guided Level-Up & Multiclass Wizard UI popup when a character levels up by milestone or XP.",
        parameters={
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "Character name to open the Level-Up Wizard for."},
                "reason": {"type": "string", "description": "Optional narrative or milestone reason (e.g. 'Defeated the Baron and reached Milestone Level 4')."}
            },
            "required": ["character_name"]
        },
        handler=prompt_level_up_wizard
    )


# ============================================================================
# LLM Tool Dispatcher for Combat & GM Adjudication (Phase 1 Engine Bindings)
# ============================================================================
from core.combat.combat_manager import CombatManager
from core.scene.scene_manager import SceneManager
from core.rulesets import get_ruleset

# Module-level singletons for session duration
_SCENE = SceneManager(campaign_id="default_campaign")
_COMBAT = CombatManager(system_id="dnd_5e")


def tool_start_combat(reported_initiatives: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Begins combat with all participants in the active scene."""
    participants = _SCENE.get_all_participants()
    if not participants:
        return {"error": "Cannot start combat: Scene has no active participants."}
    return _COMBAT.start_encounter(participants, reported_initiatives=reported_initiatives)


def tool_next_combat_turn() -> Dict[str, Any]:
    """Advances combat to the next combatant's turn."""
    return _COMBAT.next_turn()


def tool_execute_attack(
    attacker_name: str,
    target_name: str,
    weapon_name: str,
    attack_roll: Optional[int] = None,
    damage_roll: Optional[int] = None
) -> Dict[str, Any]:
    """Executes a weapon or spell attack between two combatants."""
    return _COMBAT.execute_attack(
        attacker_name,
        target_name,
        weapon_name,
        reported_atk_roll=attack_roll,
        reported_dmg_roll=damage_roll
    )


def tool_execute_party_rest(rest_type: str, dice_mode: str = "digital") -> Dict[str, Any]:
    """Triggers Short or Long Rest recovery across the active party."""
    adapter = get_ruleset(_COMBAT.system_id)
    reports = []
    for char in _SCENE.state["party_roster"]:
        rep = adapter.execute_rest(char, rest_type=rest_type, dice_mode=dice_mode)
        reports.append(rep.narrative)
    _SCENE.save_state()
    return {"rest_type": rest_type, "results": reports}


def tool_spawn_enemy(name: str, hp: int, ac: int, weapon_damage: str = "1d6+2") -> Dict[str, Any]:
    """Spawns an enemy into the current scene."""
    return _SCENE.spawn_adversary(name, hp=hp, ac=ac, damage=weapon_damage)

