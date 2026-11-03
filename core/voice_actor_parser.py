"""
core/voice_actor_parser.py - Multi-Player Single-Microphone Voice Attribution
Parses spoken utterances into structured actor actions, group consensus, or clarification states.
"""
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class ParsedTableAction:
    actor_name: Optional[str]
    character_id: Optional[str]
    clean_action_text: str
    is_direct_gm_command: bool = False
    requires_actor_clarification: bool = False
    is_group_action: bool = False
    clarification_target_verb: Optional[str] = None


class VoiceActorParser:
    def __init__(self, character_roster: Optional[Dict[str, Dict[str, Any]]] = None):
        self.roster = character_roster or {}

    def update_roster(self, new_roster: Dict[str, Dict[str, Any]]):
        self.roster = new_roster or {}

    def parse_utterance(
        self,
        raw_text: str,
        is_combat_active: bool = False,
        active_combat_char_id: Optional[str] = None,
        focused_char_id: Optional[str] = None
    ) -> ParsedTableAction:
        clean = (raw_text or "").strip()
        if not clean:
            return ParsedTableAction(None, None, "", is_direct_gm_command=False)

        # 1. Direct GM Command ("Aether, roll 2d6", "Computer, next turn")
        is_direct = False
        gm_call_match = re.match(r'^(aether|gm|storyteller|computer)[,\s:]+(.*)$', clean, re.IGNORECASE)
        if gm_call_match:
            is_direct = True
            clean = gm_call_match.group(2).strip()

        # 2. Check Explicit Character Prefacing / Third-Person Action
        for c_id, c_data in self.roster.items():
            name = c_data.get("name", "")
            if not name:
                continue

            # Case A: Explicit character prefix: "Kaelen: I attack", "Kaelen, I attack", or "As Kaelen, I attack"
            prefix_pattern = rf'^(?:as\s+{re.escape(name)}[\s,:\-]+|{re.escape(name)}\s*[:,–—\-]+\s*)(.*)$'
            match = re.match(prefix_pattern, clean, re.IGNORECASE)
            if match:
                return ParsedTableAction(
                    actor_name=name,
                    character_id=c_id,
                    clean_action_text=match.group(1).strip(),
                    is_direct_gm_command=is_direct
                )

            # Case B: Third person: "Kaelen raises his shield" or "Vespera picks the lock"
            third_person = rf'^{re.escape(name)}\s+(.*)$'
            tp_match = re.match(third_person, clean, re.IGNORECASE)
            if tp_match:
                return ParsedTableAction(
                    actor_name=name,
                    character_id=c_id,
                    clean_action_text=f"{name} {tp_match.group(1).strip()}",
                    is_direct_gm_command=is_direct
                )

        # 3. Combat Auto-Lock: In active combat, anonymous action belongs to turn-holder
        if is_combat_active and active_combat_char_id and active_combat_char_id in self.roster:
            active_name = self.roster[active_combat_char_id].get("name")
            return ParsedTableAction(
                actor_name=active_name,
                character_id=active_combat_char_id,
                clean_action_text=clean,
                is_direct_gm_command=is_direct
            )

        # 4. Group Exploration ("We head down the stairs", "Party rests")
        group_match = re.search(r'\b(we|us|our|party|everyone)\b', clean, re.IGNORECASE)
        first_person_singular = re.search(r'\b(i|me|my|myself)\b', clean, re.IGNORECASE)

        if group_match and not first_person_singular:
            return ParsedTableAction(
                actor_name=None,
                character_id=None,
                clean_action_text=clean,
                is_direct_gm_command=is_direct,
                is_group_action=True
            )

        # 5. Check if user spoke a lone character name or identity claim (resolving previous clarification)
        clean_normalized = clean.lower().rstrip(".,!? ")
        for c_id, c_data in self.roster.items():
            name = c_data.get("name", "")
            if not name:
                continue
            name_lower = name.lower()
            if clean_normalized in (name_lower, f"it's {name_lower}", f"its {name_lower}", f"i am {name_lower}", f"i'm {name_lower}"):
                return ParsedTableAction(
                    actor_name=name,
                    character_id=c_id,
                    clean_action_text="",
                    is_direct_gm_command=is_direct
                )

        # 6. Anonymous First-Person Action Requiring Clarification
        if first_person_singular:
            # Extract main verb phrase for question synthesis
            verb_candidate = "take this action"
            action_words = clean.split()
            if len(action_words) >= 2:
                # E.g., "I open the chest" -> "open the chest"
                verb_candidate = " ".join(action_words[1:5])

            return ParsedTableAction(
                actor_name=None,
                character_id=None,
                clean_action_text=clean,
                is_direct_gm_command=is_direct,
                requires_actor_clarification=True,
                clarification_target_verb=verb_candidate
            )

        # 7. Unattributed Generic Statement (Likely Table Talk or Ambiguous)
        fallback_id = focused_char_id if (focused_char_id and focused_char_id in self.roster) else None
        fallback_name = self.roster[fallback_id].get("name") if fallback_id else None

        return ParsedTableAction(
            actor_name=fallback_name,
            character_id=fallback_id,
            clean_action_text=clean,
            is_direct_gm_command=is_direct
        )
