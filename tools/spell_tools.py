"""
tools/spell_tools.py - Agent Tools for Deterministic Spell & Magic Adjudication
Supports Vancian slots, Pathfinder 2e Focus Magic, and Call of Cthulhu MP/SAN expenditure.
"""
from typing import Dict, Any, Optional
from core.ttrpg_service import TTRPGService


def register_spell_tools(dispatcher, service: TTRPGService):

    async def cast_pathfinder_spell(
        character_name: str,
        spell_name: str,
        spell_rank: int,
        is_focus_spell: bool = False
    ) -> Dict[str, Any]:
        """
        Deducts a spell slot or focus point when a Pathfinder 2e character casts a spell.
        """
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

        # Slot Deduction
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

    async def cast_coc_spell(
        character_name: str,
        spell_name: str,
        mp_cost: int,
        san_loss_formula: str = "1d4"
    ) -> Dict[str, Any]:
        """
        Deducts Magic Points and rolls Sanity loss when casting a Call of Cthulhu occult ritual.
        """
        char_id, char = service._find_character(character_name)
        if not char:
            return {"status": "error", "message": f"Investigator '{character_name}' not found."}

        # Deduct MP
        vitals = char.get("vitals", {})
        mp_data = vitals.get("magic_points", {})
        cur_mp = mp_data.get("current", 0)

        if cur_mp < mp_cost:
            return {"status": "error", "message": f"Insufficient Magic Points! Needs {mp_cost} MP, has {cur_mp} MP."}

        mp_data["current"] = cur_mp - mp_cost

        # Roll and apply SAN loss
        roll_res = service.parse_and_roll(san_loss_formula)
        san_loss = roll_res["total"] if roll_res.get("status") == "success" else 1

        san_data = vitals.get("sanity", {})
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

    # Register tools
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
                "san_loss_formula": {"type": "string", "description": "Sanity loss dice (e.g. '1d4', '1d6')."}
            },
            "required": ["character_name", "spell_name", "mp_cost"]
        },
        handler=cast_coc_spell
    )

