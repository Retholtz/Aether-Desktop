"""
core/parsers/spell_registry.py - D&D 5e / 2024 Spell Knowledge Registry & Sanitizer
Provides structured parsing, damage formulas, level resolution, and header filtering
for TTRPG character sheet imports.
"""
import re
from typing import Dict, Any, List, Optional

# Regex patterns matching table headers, page citations, duration/component fragments,
# slot counters, and AcroForm column labels that must NEVER be parsed as individual spells.
JUNK_SPELL_PATTERNS = [
    r"^===",
    r"^\(",
    r"^\d+\s*slots?",
    r"^phb-",
    r"^dmg-",
    r"^xge-",
    r"^tce-",
    r"^mm-",
    r"^scag-",
    r"^page\s*ref",
    r"^source$",
    r"^save/atk",
    r"^time$",
    r"^range$",
    r"^comp$",
    r"^duration$",
    r"^prep(?:ared)?$",
    r"^spell\s*name$",
    r"^spells$",
    r"^notes$",
    r"^casting\s*time",
    r"^d:\s*",
    r"^(?:v|s|m)(?:[,\s/]+(?:v|s|m))*$",
    r"^(?:instantaneous|concentration|\d+\s*(?:hour|minute|round|day)s?)$",
    r"^(?:touch|self|\d+\s*ft\.?)$",
    r"^(?:touch|self)/\d+\s*ft\..*$",
    r"^(?:touch|self)/.*cone.*$",
    r"^(?:touch|self)/.*sphere.*$",
    r"^(?:self/\d+\s*ft\.?.*)$",
    r"^(?:dex|con|str|int|wis|cha)\s*\d+$",
    r"^\d+\s*ft\.\s*(?:cone|sphere|radius|line|cube).*$",
    r"^concentration.*$",
    r"^--$",
    r"^at\s*will$"
]

def is_junk_spell(s_name: str) -> bool:
    """Returns True if the string is a table header, rule citation, or metadata token rather than a real spell."""
    if not s_name or not isinstance(s_name, str):
        return True
    s_clean = s_name.strip().lower()
    if len(s_clean) < 2:
        return True
    for pat in JUNK_SPELL_PATTERNS:
        if re.search(pat, s_clean):
            return True
    return False


# Comprehensive canonical D&D 5e / 2024 Spell Registry with levels, damage formulas, and ranges
DND_SPELL_REGISTRY: Dict[str, Dict[str, str]] = {
    # Cantrips
    "acid splash": {"level": "Cantrip", "damage": "1d6 Acid", "range": "60 ft."},
    "blade ward": {"level": "Cantrip", "damage": "", "range": "Self"},
    "booming blade": {"level": "Cantrip", "damage": "1d8 Thunder", "range": "Self/5 ft."},
    "chill touch": {"level": "Cantrip", "damage": "1d10 Necrotic", "range": "120 ft."},
    "control flames": {"level": "Cantrip", "damage": "", "range": "60 ft."},
    "create bonfire": {"level": "Cantrip", "damage": "1d8 Fire", "range": "60 ft."},
    "dancing lights": {"level": "Cantrip", "damage": "", "range": "120 ft."},
    "druidcraft": {"level": "Cantrip", "damage": "", "range": "30 ft."},
    "eldritch blast": {"level": "Cantrip", "damage": "1d10 Force", "range": "120 ft."},
    "fire bolt": {"level": "Cantrip", "damage": "1d10 Fire", "range": "120 ft."},
    "friends": {"level": "Cantrip", "damage": "", "range": "Self"},
    "frostbite": {"level": "Cantrip", "damage": "1d6 Cold", "range": "60 ft."},
    "green-flame blade": {"level": "Cantrip", "damage": "1d8 Fire", "range": "Self/5 ft."},
    "guidance": {"level": "Cantrip", "damage": "", "range": "Touch"},
    "gust": {"level": "Cantrip", "damage": "", "range": "30 ft."},
    "infestation": {"level": "Cantrip", "damage": "1d6 Poison", "range": "30 ft."},
    "light": {"level": "Cantrip", "damage": "", "range": "Touch"},
    "lightning lure": {"level": "Cantrip", "damage": "1d8 Lightning", "range": "15 ft."},
    "mage hand": {"level": "Cantrip", "damage": "", "range": "30 ft."},
    "mending": {"level": "Cantrip", "damage": "", "range": "Touch"},
    "message": {"level": "Cantrip", "damage": "", "range": "120 ft."},
    "mind sliver": {"level": "Cantrip", "damage": "1d6 Psychic", "range": "60 ft."},
    "minor illusion": {"level": "Cantrip", "damage": "", "range": "30 ft."},
    "mold earth": {"level": "Cantrip", "damage": "", "range": "30 ft."},
    "poison spray": {"level": "Cantrip", "damage": "1d12 Poison", "range": "10 ft."},
    "prestidigitation": {"level": "Cantrip", "damage": "", "range": "10 ft."},
    "primal savagery": {"level": "Cantrip", "damage": "1d10 Acid", "range": "Self"},
    "produce flame": {"level": "Cantrip", "damage": "1d8 Fire", "range": "Self/30 ft."},
    "ray of frost": {"level": "Cantrip", "damage": "1d8 Cold", "range": "60 ft."},
    "resistance": {"level": "Cantrip", "damage": "", "range": "Touch"},
    "sacred flame": {"level": "Cantrip", "damage": "1d8 Radiant", "range": "60 ft."},
    "shape water": {"level": "Cantrip", "damage": "", "range": "30 ft."},
    "shillelagh": {"level": "Cantrip", "damage": "1d8 Bludgeoning", "range": "Touch"},
    "shocking grasp": {"level": "Cantrip", "damage": "1d8 Lightning", "range": "Touch"},
    "sorcerous burst": {"level": "Cantrip", "damage": "1d8 Acid", "range": "120 ft."},
    "spare the dying": {"level": "Cantrip", "damage": "", "range": "Touch"},
    "sword burst": {"level": "Cantrip", "damage": "1d6 Force", "range": "Self/5 ft."},
    "starry wisp": {"level": "Cantrip", "damage": "1d8 Radiant", "range": "60 ft."},
    "thaumaturgy": {"level": "Cantrip", "damage": "", "range": "30 ft."},
    "thorn whip": {"level": "Cantrip", "damage": "1d6 Piercing", "range": "30 ft."},
    "thunderclap": {"level": "Cantrip", "damage": "1d6 Thunder", "range": "Self/5 ft."},
    "toll the dead": {"level": "Cantrip", "damage": "1d8 / 1d12 Necrotic", "range": "60 ft."},
    "true strike": {"level": "Cantrip", "damage": "", "range": "30 ft."},
    "vicious mockery": {"level": "Cantrip", "damage": "1d4 Psychic", "range": "60 ft."},
    "word of radiance": {"level": "Cantrip", "damage": "1d6 Radiant", "range": "Self/5 ft."},

    # 1st Level
    "absorb elements": {"level": "1st Level", "damage": "", "range": "Self"},
    "alarm": {"level": "1st Level", "damage": "", "range": "30 ft."},
    "animal friendship": {"level": "1st Level", "damage": "", "range": "30 ft."},
    "armor of agathys": {"level": "1st Level", "damage": "5 Cold", "range": "Self"},
    "arms of hadar": {"level": "1st Level", "damage": "2d6 Necrotic", "range": "Self/10 ft."},
    "bane": {"level": "1st Level", "damage": "", "range": "30 ft."},
    "bless": {"level": "1st Level", "damage": "", "range": "30 ft."},
    "burning hands": {"level": "1st Level", "damage": "3d6 Fire", "range": "Self/15 ft. Cone"},
    "catapult": {"level": "1st Level", "damage": "3d8 Bludgeoning", "range": "90 ft."},
    "cause fear": {"level": "1st Level", "damage": "", "range": "60 ft."},
    "ceremony": {"level": "1st Level", "damage": "", "range": "Touch"},
    "chaos bolt": {"level": "1st Level", "damage": "2d8+1d6", "range": "120 ft."},
    "charm person": {"level": "1st Level", "damage": "", "range": "30 ft."},
    "chromatic orb": {"level": "1st Level", "damage": "3d8", "range": "90 ft."},
    "color spray": {"level": "1st Level", "damage": "", "range": "Self/15 ft. Cone"},
    "command": {"level": "1st Level", "damage": "", "range": "60 ft."},
    "comprehend languages": {"level": "1st Level", "damage": "", "range": "Self"},
    "cure wounds": {"level": "1st Level", "damage": "1d8+Mod Healing", "range": "Touch"},
    "detect evil and good": {"level": "1st Level", "damage": "", "range": "Self/30 ft."},
    "detect magic": {"level": "1st Level", "damage": "", "range": "Self/30 ft. Sphere"},
    "detect magic [r]": {"level": "1st Level", "damage": "", "range": "Self/30 ft. Sphere"},
    "detect poison and disease": {"level": "1st Level", "damage": "", "range": "Self/30 ft."},
    "disguise self": {"level": "1st Level", "damage": "", "range": "Self"},
    "dissonant whispers": {"level": "1st Level", "damage": "3d6 Psychic", "range": "60 ft."},
    "divine favor": {"level": "1st Level", "damage": "1d4 Radiant", "range": "Self"},
    "earth tremor": {"level": "1st Level", "damage": "1d6 Bludgeoning", "range": "Self/10 ft."},
    "ensnaring strike": {"level": "1st Level", "damage": "1d6 Piercing", "range": "Self"},
    "entangle": {"level": "1st Level", "damage": "", "range": "90 ft."},
    "expeditious retreat": {"level": "1st Level", "damage": "", "range": "Self"},
    "faerie fire": {"level": "1st Level", "damage": "", "range": "60 ft."},
    "false life": {"level": "1st Level", "damage": "", "range": "Self"},
    "feather fall": {"level": "1st Level", "damage": "", "range": "60 ft."},
    "find familiar": {"level": "1st Level", "damage": "", "range": "10 ft."},
    "fog cloud": {"level": "1st Level", "damage": "", "range": "120 ft."},
    "goodberry": {"level": "1st Level", "damage": "", "range": "Touch"},
    "grease": {"level": "1st Level", "damage": "", "range": "60 ft."},
    "guiding bolt": {"level": "1st Level", "damage": "4d6 Radiant", "range": "120 ft."},
    "healing word": {"level": "1st Level", "damage": "1d4+Mod Healing", "range": "60 ft."},
    "hellish rebuke": {"level": "1st Level", "damage": "2d10 Fire", "range": "60 ft."},
    "heroism": {"level": "1st Level", "damage": "", "range": "Touch"},
    "hex": {"level": "1st Level", "damage": "1d6 Necrotic", "range": "90 ft."},
    "hunter's mark": {"level": "1st Level", "damage": "1d6", "range": "90 ft."},
    "ice knife": {"level": "1st Level", "damage": "1d10 Piercing / 2d6 Cold", "range": "60 ft."},
    "identify": {"level": "1st Level", "damage": "", "range": "Touch"},
    "illusory script": {"level": "1st Level", "damage": "", "range": "Touch"},
    "inflict wounds": {"level": "1st Level", "damage": "3d10 Necrotic", "range": "Touch"},
    "jump": {"level": "1st Level", "damage": "", "range": "Touch"},
    "longstrider": {"level": "1st Level", "damage": "", "range": "Touch"},
    "mage armor": {"level": "1st Level", "damage": "", "range": "Touch"},
    "magic missile": {"level": "1st Level", "damage": "3x (1d4+1) Force", "range": "120 ft."},
    "protection from evil and good": {"level": "1st Level", "damage": "", "range": "Touch"},
    "purify food and drink": {"level": "1st Level", "damage": "", "range": "10 ft."},
    "ray of sickness": {"level": "1st Level", "damage": "2d8 Poison", "range": "60 ft."},
    "sanctuary": {"level": "1st Level", "damage": "", "range": "30 ft."},
    "shield": {"level": "1st Level", "damage": "", "range": "Self"},
    "shield of faith": {"level": "1st Level", "damage": "", "range": "60 ft."},
    "silent image": {"level": "1st Level", "damage": "", "range": "60 ft."},
    "sleep": {"level": "1st Level", "damage": "", "range": "90 ft."},
    "snare": {"level": "1st Level", "damage": "", "range": "Touch"},
    "speak with animals": {"level": "1st Level", "damage": "", "range": "Self"},
    "tasha's hideous laughter": {"level": "1st Level", "damage": "", "range": "30 ft."},
    "thunderous smite": {"level": "1st Level", "damage": "2d6 Thunder", "range": "Self"},
    "thunderwave": {"level": "1st Level", "damage": "2d8 Thunder", "range": "Self/15 ft. Cube"},
    "unseen servant": {"level": "1st Level", "damage": "", "range": "60 ft."},
    "witch bolt": {"level": "1st Level", "damage": "1d12 Lightning", "range": "30 ft."},
    "wrathful smite": {"level": "1st Level", "damage": "1d6 Psychic", "range": "Self"},
    "zephyr strike": {"level": "1st Level", "damage": "1d8 Force", "range": "Self"},

    # 2nd Level
    "aid": {"level": "2nd Level", "damage": "", "range": "30 ft."},
    "alter self": {"level": "2nd Level", "damage": "", "range": "Self"},
    "animal messenger": {"level": "2nd Level", "damage": "", "range": "30 ft."},
    "arcane lock": {"level": "2nd Level", "damage": "", "range": "Touch"},
    "augury": {"level": "2nd Level", "damage": "", "range": "Self"},
    "barkskin": {"level": "2nd Level", "damage": "", "range": "Touch"},
    "beast sense": {"level": "2nd Level", "damage": "", "range": "Touch"},
    "blindness/deafness": {"level": "2nd Level", "damage": "", "range": "30 ft."},
    "blur": {"level": "2nd Level", "damage": "", "range": "Self"},
    "branding smite": {"level": "2nd Level", "damage": "2d6 Radiant", "range": "Self"},
    "calm emotions": {"level": "2nd Level", "damage": "", "range": "60 ft."},
    "cloud of daggers": {"level": "2nd Level", "damage": "4d4 Slashing", "range": "60 ft."},
    "continual flame": {"level": "2nd Level", "damage": "", "range": "Touch"},
    "cordon of arrows": {"level": "2nd Level", "damage": "1d6 Piercing", "range": "5 ft."},
    "crown of madness": {"level": "2nd Level", "damage": "", "range": "120 ft."},
    "darkness": {"level": "2nd Level", "damage": "", "range": "60 ft."},
    "darkvision": {"level": "2nd Level", "damage": "", "range": "Touch"},
    "detect thoughts": {"level": "2nd Level", "damage": "", "range": "Self"},
    "dragon's breath": {"level": "2nd Level", "damage": "3d6", "range": "Touch/15 ft. Cone"},
    "dust devil": {"level": "2nd Level", "damage": "1d8 Bludgeoning", "range": "60 ft."},
    "earthbind": {"level": "2nd Level", "damage": "", "range": "300 ft."},
    "enhance ability": {"level": "2nd Level", "damage": "", "range": "Touch"},
    "enlarge/reduce": {"level": "2nd Level", "damage": "1d4", "range": "30 ft."},
    "enthrall": {"level": "2nd Level", "damage": "", "range": "60 ft."},
    "find steed": {"level": "2nd Level", "damage": "", "range": "30 ft."},
    "find traps": {"level": "2nd Level", "damage": "", "range": "120 ft."},
    "flame blade": {"level": "2nd Level", "damage": "3d6 Fire", "range": "Self"},
    "flaming sphere": {"level": "2nd Level", "damage": "2d6 Fire", "range": "60 ft."},
    "gentle repose": {"level": "2nd Level", "damage": "", "range": "Touch"},
    "gust of wind": {"level": "2nd Level", "damage": "", "range": "Self/60 ft. Line"},
    "healing spirit": {"level": "2nd Level", "damage": "1d6 Healing", "range": "60 ft."},
    "heat metal": {"level": "2nd Level", "damage": "2d8 Fire", "range": "60 ft."},
    "hold person": {"level": "2nd Level", "damage": "", "range": "60 ft."},
    "invisibility": {"level": "2nd Level", "damage": "", "range": "Touch"},
    "knock": {"level": "2nd Level", "damage": "", "range": "60 ft."},
    "lesser restoration": {"level": "2nd Level", "damage": "", "range": "Touch"},
    "levitate": {"level": "2nd Level", "damage": "", "range": "60 ft."},
    "locate animals or plants": {"level": "2nd Level", "damage": "", "range": "Self"},
    "locate object": {"level": "2nd Level", "damage": "", "range": "Self"},
    "magic mouth": {"level": "2nd Level", "damage": "", "range": "30 ft."},
    "magic weapon": {"level": "2nd Level", "damage": "+1 Bonus", "range": "Touch"},
    "melf's acid arrow": {"level": "2nd Level", "damage": "4d4 Acid", "range": "90 ft."},
    "mind spike": {"level": "2nd Level", "damage": "3d8 Psychic", "range": "60 ft."},
    "mirror image": {"level": "2nd Level", "damage": "", "range": "Self"},
    "misty step": {"level": "2nd Level", "damage": "", "range": "Self"},
    "moonbeam": {"level": "2nd Level", "damage": "2d10 Radiant", "range": "120 ft."},
    "pass without trace": {"level": "2nd Level", "damage": "", "range": "Self"},
    "phantasmal force": {"level": "2nd Level", "damage": "1d6 Psychic", "range": "60 ft."},
    "prayer of healing": {"level": "2nd Level", "damage": "2d8+Mod Healing", "range": "30 ft."},
    "protection from poison": {"level": "2nd Level", "damage": "", "range": "Touch"},
    "ray of enfeeblement": {"level": "2nd Level", "damage": "", "range": "60 ft."},
    "rope trick": {"level": "2nd Level", "damage": "", "range": "Touch"},
    "scorching ray": {"level": "2nd Level", "damage": "2d6 Fire per ray", "range": "120 ft."},
    "see invisibility": {"level": "2nd Level", "damage": "", "range": "Self"},
    "shadow blade": {"level": "2nd Level", "damage": "2d8 Psychic", "range": "Self"},
    "shatter": {"level": "2nd Level", "damage": "3d8 Thunder", "range": "60 ft."},
    "silence": {"level": "2nd Level", "damage": "", "range": "120 ft."},
    "spider climb": {"level": "2nd Level", "damage": "", "range": "Touch"},
    "spiritual weapon": {"level": "2nd Level", "damage": "1d8+Mod Force", "range": "60 ft."},
    "suggestion": {"level": "2nd Level", "damage": "", "range": "30 ft."},
    "warding bond": {"level": "2nd Level", "damage": "", "range": "Touch"},
    "web": {"level": "2nd Level", "damage": "", "range": "60 ft."},
    "zone of truth": {"level": "2nd Level", "damage": "", "range": "60 ft."},

    # 3rd Level + higher common spells
    "animate dead": {"level": "3rd Level", "damage": "", "range": "10 ft."},
    "call lightning": {"level": "3rd Level", "damage": "3d10 Lightning", "range": "120 ft."},
    "counterspell": {"level": "3rd Level", "damage": "", "range": "60 ft."},
    "dispel magic": {"level": "3rd Level", "damage": "", "range": "120 ft."},
    "fireball": {"level": "3rd Level", "damage": "8d6 Fire", "range": "150 ft."},
    "fly": {"level": "3rd Level", "damage": "", "range": "Touch"},
    "gaseous form": {"level": "3rd Level", "damage": "", "range": "Touch"},
    "haste": {"level": "3rd Level", "damage": "", "range": "30 ft."},
    "hypnotic pattern": {"level": "3rd Level", "damage": "", "range": "120 ft."},
    "lightning bolt": {"level": "3rd Level", "damage": "8d6 Lightning", "range": "Self/100 ft. Line"},
    "major image": {"level": "3rd Level", "damage": "", "range": "120 ft."},
    "mass healing word": {"level": "3rd Level", "damage": "1d4+Mod Healing", "range": "60 ft."},
    "revivify": {"level": "3rd Level", "damage": "", "range": "Touch"},
    "slow": {"level": "3rd Level", "damage": "", "range": "120 ft."},
    "spirit guardians": {"level": "3rd Level", "damage": "3d8 Radiant/Necrotic", "range": "Self/15 ft."},
    "stinking cloud": {"level": "3rd Level", "damage": "", "range": "90 ft."},
    "tongues": {"level": "3rd Level", "damage": "", "range": "Touch"},
    "vampiric touch": {"level": "3rd Level", "damage": "3d6 Necrotic", "range": "Self"},
    "water breathing": {"level": "3rd Level", "damage": "", "range": "30 ft."},

    # 4th Level
    "banishment": {"level": "4th Level", "damage": "", "range": "60 ft."},
    "blight": {"level": "4th Level", "damage": "8d8 Necrotic", "range": "30 ft."},
    "dimension door": {"level": "4th Level", "damage": "", "range": "500 ft."},
    "greater invisibility": {"level": "4th Level", "damage": "", "range": "Touch"},
    "ice storm": {"level": "4th Level", "damage": "2d8 Bludgeoning + 4d6 Cold", "range": "300 ft."},
    "polymorph": {"level": "4th Level", "damage": "", "range": "60 ft."},
    "wall of fire": {"level": "4th Level", "damage": "5d8 Fire", "range": "120 ft."},

    # 5th Level
    "cloudkill": {"level": "5th Level", "damage": "5d8 Poison", "range": "120 ft."},
    "cone of cold": {"level": "5th Level", "damage": "8d8 Cold", "range": "Self/60 ft. Cone"},
    "greater restoration": {"level": "5th Level", "damage": "", "range": "Touch"},
    "hold monster": {"level": "5th Level", "damage": "", "range": "90 ft."},
    "mass cure wounds": {"level": "5th Level", "damage": "3d8+Mod Healing", "range": "60 ft."},
    "raise dead": {"level": "5th Level", "damage": "", "range": "Touch"},
    "scrying": {"level": "5th Level", "damage": "", "range": "Self"},
    "synaptic static": {"level": "5th Level", "damage": "8d6 Psychic", "range": "120 ft."},
    "telekinesis": {"level": "5th Level", "damage": "", "range": "60 ft."},
    "wall of force": {"level": "5th Level", "damage": "", "range": "120 ft."},

    # 6th+ Level
    "chain lightning": {"level": "6th Level", "damage": "10d8 Lightning", "range": "150 ft."},
    "disintegrate": {"level": "6th Level", "damage": "10d6+40 Force", "range": "60 ft."},
    "heal": {"level": "6th Level", "damage": "70 HP Healing", "range": "60 ft."},
    "delayed blast fireball": {"level": "7th Level", "damage": "12d6 Fire", "range": "150 ft."},
    "finger of death": {"level": "7th Level", "damage": "7d8+30 Necrotic", "range": "60 ft."},
    "teleport": {"level": "7th Level", "damage": "", "range": "10 ft."},
    "sunburst": {"level": "8th Level", "damage": "12d6 Radiant", "range": "150 ft."},
    "meteor swarm": {"level": "9th Level", "damage": "20d6 Fire + 20d6 Bludgeoning", "range": "1 mile"},
    "power word kill": {"level": "9th Level", "damage": "Instant Death (<100 HP)", "range": "60 ft."},
    "time stop": {"level": "9th Level", "damage": "", "range": "Self"},
    "wish": {"level": "9th Level", "damage": "", "range": "Self"}
}


def normalize_spell_level(lvl_val: Any) -> int:
    """Converts a spell level token ('Cantrip', '1st Level', '2', 0) to a deterministic integer."""
    if isinstance(lvl_val, int):
        return lvl_val
    s = str(lvl_val or "").strip().lower()
    if "cantrip" in s or s == "0":
        return 0
    m = re.search(r'\d+', s)
    return int(m.group(0)) if m else 1


def parse_structured_spells(fields: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parses AcroForm fields (including D&D Beyond 2024 / 2014 exports and standard WotC sheets)
    into a clean list of structured spell dicts.
    Filters out slot counters, table headers, column labels, and page citations.
    """
    spells: List[Dict[str, Any]] = []
    seen_names = set()

    # 1. Map weapon attacks on page 1 that contain spell damage dice
    wpn_damage_map: Dict[str, str] = {}
    for i in range(1, 15):
        w_name = fields.get(f"Wpn Name {i}") or fields.get(f"Wpn Name{i}") or (fields.get("Wpn Name") if i == 1 else None)
        w_dmg = fields.get(f"Wpn{i} Damage") or fields.get(f"Wpn {i} Damage") or (fields.get("Wpn1 Damage") if i == 1 else None)
        if w_name and w_dmg:
            w_n_clean = str(w_name).strip().lower()
            wpn_damage_map[w_n_clean] = str(w_dmg).strip()

    header_regex = re.compile(r"===\s*(\d+(?:st|nd|rd|th)?\s*level|cantrips?)\s*===", re.IGNORECASE)
    current_level = "Cantrip"

    # 2. Iterate sequentially through form fields to track active level and spell index
    for k, v in fields.items():
        v_str = str(v).strip()

        # Check for level header transitions (e.g. === CANTRIPS === or === 1st LEVEL ===)
        m_head = header_regex.search(v_str)
        if m_head:
            hl = m_head.group(1).lower()
            if "cantrip" in hl:
                current_level = "Cantrip"
            else:
                num_m = re.search(r"\d+", hl)
                if num_m:
                    n = int(num_m.group(0))
                    suff = "th" if 11 <= n <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
                    current_level = f"{n}{suff} Level"
                else:
                    current_level = m_head.group(1).title()

        # D&D Beyond indexed field pattern: spellName0, spellName1, ...
        m_name = re.match(r"^spellname(\d+)$", k, re.IGNORECASE)
        if m_name:
            idx = m_name.group(1)
            name = v_str
            if is_junk_spell(name):
                continue

            lower_name = name.lower()
            clean_key = re.sub(r"\s*(?:\[.*?\]|<.*?>|\(.*?\))", "", name).strip().lower()
            if not clean_key or clean_key in seen_names:
                continue
            seen_names.add(clean_key)

            save_hit = str(fields.get(f"spellSaveHit{idx}") or fields.get(f"spellSaveHit {idx}") or "").strip()
            casting_time = str(fields.get(f"spellCastingTime{idx}") or fields.get(f"spellCastingTime {idx}") or "").strip()
            range_val = str(fields.get(f"spellRange{idx}") or fields.get(f"spellRange {idx}") or "").strip()
            components = str(fields.get(f"spellComponents{idx}") or fields.get(f"spellComponents {idx}") or "").strip()
            duration = str(fields.get(f"spellDuration{idx}") or fields.get(f"spellDuration {idx}") or "").strip()
            notes = str(fields.get(f"spellNotes{idx}") or fields.get(f"spellNotes {idx}") or "").strip()

            reg_info = DND_SPELL_REGISTRY.get(lower_name) or DND_SPELL_REGISTRY.get(clean_key) or {}

            # Level resolution
            lvl = reg_info.get("level") or current_level or "Cantrip/Spell"

            # Damage resolution (from sheet weapon attack or registry)
            dmg = wpn_damage_map.get(lower_name) or wpn_damage_map.get(clean_key) or reg_info.get("damage", "")

            # Range resolution
            rng = range_val or reg_info.get("range", "60 ft.")

            # Clean notes assembly
            note_parts = []
            if casting_time and casting_time not in ("1A", "1 action", "1 Action"):
                note_parts.append("Bonus Action" if casting_time in ("1BA", "1ba") else casting_time)
            if components:
                note_parts.append(components)
            if duration and duration != "--":
                note_parts.append(duration)
            if notes and not notes.lower().startswith("d:") and notes not in (components, duration):
                note_parts.append(notes)
            full_notes = " | ".join(dict.fromkeys(note_parts))

            spells.append({
                "name": name,
                "level": normalize_spell_level(lvl),
                "atk_bonus": save_hit if save_hit != "--" else "",
                "damage": dmg,
                "range": rng,
                "notes": full_notes,
                "is_ritual": "[r]" in lower_name
            })

    # 3. Fallback for non-D&D Beyond standard WotC AcroForms (e.g. Spells 1014, SpellName1, etc.)
    if not spells:
        for k, v in fields.items():
            k_lower = k.lower()
            # Only match fields designated specifically for spell names
            if re.search(r"^(?:spells?\s*\d+|spellname\s*\d+|spell_\d+)$", k_lower):
                v_str = str(v).strip()
                if is_junk_spell(v_str):
                    continue
                for line in v_str.splitlines():
                    clean_line = line.strip().lstrip("-•* ")
                    if not clean_line or is_junk_spell(clean_line):
                        continue
                    low_l = clean_line.lower()
                    clean_key = re.sub(r"\s*(?:\[.*?\]|<.*?>|\(.*?\))", "", clean_line).strip().lower()
                    if not clean_key or clean_key in seen_names:
                        continue
                    seen_names.add(clean_key)

                    reg_info = DND_SPELL_REGISTRY.get(low_l) or DND_SPELL_REGISTRY.get(clean_key) or {}

                    spells.append({
                        "name": clean_line,
                        "level": normalize_spell_level(reg_info.get("level", "Cantrip/Spell")),
                        "atk_bonus": "",
                        "damage": reg_info.get("damage", ""),
                        "range": reg_info.get("range", "60 ft."),
                        "notes": "",
                        "is_ritual": "[r]" in low_l
                    })

    return spells


def sanitize_parsed_spells(
    raw_spells: List[Any],
    default_dc: str = "",
    default_atk: str = ""
) -> List[Dict[str, Any]]:
    """
    Sanitizes any raw list of spells (whether dicts or strings), dropping junk tokens,
    headers, citations, and normalizing into structured dictionaries.
    """
    if not raw_spells:
        return []

    sanitized: List[Dict[str, Any]] = []
    seen_names = set()

    for s in raw_spells:
        if isinstance(s, dict):
            name = str(s.get("name") or "").strip()
            if is_junk_spell(name):
                continue
            low_name = name.lower()
            clean_key = re.sub(r"\s*(?:\[.*?\]|<.*?>|\(.*?\))", "", name).strip().lower()
            if not clean_key or clean_key in seen_names:
                continue
            seen_names.add(clean_key)

            reg_info = DND_SPELL_REGISTRY.get(low_name) or DND_SPELL_REGISTRY.get(clean_key) or {}

            lvl = s.get("level")
            if not lvl or lvl in ("Cantrip/Spell", "Unknown", ""):
                lvl = reg_info.get("level", "Cantrip/Spell")

            dmg = s.get("damage")
            if not dmg:
                dmg = reg_info.get("damage", "")

            rng = s.get("range")
            if not rng or rng == "60 ft.":
                rng = reg_info.get("range") or rng or "60 ft."

            atk = s.get("atk_bonus") or s.get("save_dc") or ""

            entry = {
                "name": name,
                "level": normalize_spell_level(lvl),
                "atk_bonus": atk,
                "damage": dmg,
                "range": rng,
                "notes": str(s.get("notes") or "")
            }
            for extra_k in ("is_cantrip", "is_ritual", "is_always_prepared", "prepared"):
                if extra_k in s:
                    entry[extra_k] = s[extra_k]
            if "[r]" in low_name and "is_ritual" not in entry:
                entry["is_ritual"] = True
            sanitized.append(entry)
        elif isinstance(s, str):
            clean_str = s.strip().lstrip("-•* ")
            if is_junk_spell(clean_str):
                continue

            # Strip parenthetical annotations if they exist (e.g. "Fire Bolt (1d10)")
            dmg_match = re.search(r"\((\d+d\d+(?:\s*[-+]\s*\d+)?(?:\s+[A-Za-z]+)?)\)", clean_str, re.IGNORECASE)
            base_name = re.sub(r"\s*\([^)]*\)", "", clean_str).strip()
            if is_junk_spell(base_name):
                continue

            low_base = base_name.lower()
            if low_base in seen_names:
                continue
            seen_names.add(low_base)

            clean_key = re.sub(r"\s*\[.*?\]", "", base_name).strip().lower()
            reg_info = DND_SPELL_REGISTRY.get(low_base) or DND_SPELL_REGISTRY.get(clean_key) or {}

            dmg = (dmg_match.group(1) if dmg_match else "") or reg_info.get("damage", "")
            lvl = reg_info.get("level", "Cantrip/Spell")
            rng = reg_info.get("range", "60 ft.")

            sanitized.append({
                "name": base_name,
                "level": lvl,
                "atk_bonus": "",
                "damage": dmg,
                "range": rng,
                "notes": ""
            })

    return sanitized


def is_spell_name(name: str) -> bool:
    """Returns True if the given name matches a known canonical spell or cantrip."""
    if not name or not isinstance(name, str):
        return False
    clean = re.sub(r"\s*\[.*?\]", "", name).strip().lower()
    return clean in DND_SPELL_REGISTRY


def resolve_dnd_stat(score_val: Any, mod_val: Any, default: int = 10) -> int:
    """
    Resolves true D&D 5e ability score (1-30) vs modifier (-5 to +10).
    Handles D&D Beyond PDF exports where 'Modifier Top' option puts modifier
    in STR/DEX/CON/INT/WIS/CHA and base score in STRmod/DEXmod/CONmod/etc.
    """
    def _to_int(v: Any) -> Optional[int]:
        if v is None:
            return None
        s = str(v).strip()
        m = re.search(r"[-+]?\d+", s)
        return int(m.group(0)) if m else None

    int_a = _to_int(score_val)
    int_b = _to_int(mod_val)

    if int_a is None and int_b is None:
        return default
    if int_a is not None and int_b is None:
        return int_a
    if int_b is not None and int_a is None:
        return int_b

    # Both values exist: determine which is score (1-30) and which is modifier
    # Check mathematical relationship: modifier == (score - 10) // 2
    if 1 <= int_b <= 30 and (int_b - 10) // 2 == int_a:
        return int_b
    if 1 <= int_a <= 30 and (int_a - 10) // 2 == int_b:
        return int_a

    # Heuristic: Scores are larger numbers (usually 6-30), modifiers are small (-5 to +10)
    str_a = str(score_val).strip()
    str_b = str(mod_val).strip()
    if int_b > int_a and (int_a <= 5 or str_a.startswith(("+", "-"))):
        return int_b
    if int_a > int_b and (int_b <= 5 or str_b.startswith(("+", "-"))):
        return int_a

    return int_a if 1 <= int_a <= 30 else (int_b if 1 <= int_b <= 30 else default)


def sanitize_character_weapons(
    weapons: List[Dict[str, Any]],
    spells: Optional[List[Any]] = None
) -> List[Dict[str, Any]]:
    """
    Filters out any spell or cantrip attacks (e.g. Shocking Grasp, Sorcerous Burst, Fire Bolt)
    that D&D Beyond exports into the 'WEAPON ATTACKS & CANTRIPS' section on Page 1.
    Ensures the weapons shelf contains only physical weapons, natural attacks, or unarmed strikes.
    """
    if not weapons:
        return []

    spell_names = set()
    if spells:
        for s in spells:
            if isinstance(s, dict) and s.get("name"):
                s_name = str(s["name"]).strip().lower()
                spell_names.add(s_name)
                clean_s = re.sub(r"\s*\[.*?\]", "", s_name).strip().lower()
                spell_names.add(clean_s)
            elif isinstance(s, str) and s.strip():
                s_name = s.strip().lower()
                spell_names.add(s_name)
                clean_s = re.sub(r"\s*\[.*?\]", "", s_name).strip().lower()
                spell_names.add(clean_s)

    clean_weapons = []
    for w in weapons:
        if not isinstance(w, dict):
            continue
        w_name = str(w.get("name") or "").strip()
        if not w_name or is_junk_spell(w_name):
            continue

        low_name = w_name.lower()
        clean_name = re.sub(r"\s*\[.*?\]", "", w_name).strip().lower()

        # Check if this attack entry is actually a spell
        notes = str(w.get("notes") or "").strip()
        is_spell = (
            low_name in spell_names
            or clean_name in spell_names
            or is_spell_name(w_name)
            or bool(re.match(r"^(?:v|s|m)(?:[,\s/]+(?:v|s|m))*$", notes, re.IGNORECASE))
        )

        if not is_spell:
            clean_weapons.append(w)

    return clean_weapons


