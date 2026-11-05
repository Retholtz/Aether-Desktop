"""
core/parsers/base_parser.py - Abstract Base Parser & Normalized Data Model
Defines UniversalCharacterCore and the interface for system-specific PDF/AcroForm parsers.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple
import io
import re
from core.logger import get_logger
from core.project_service import ProjectService

logger = get_logger("BaseParser")


@dataclass
class UniversalCharacterCore:
    name: str
    player_name: str
    archetype: str
    level: int
    # 0-100 Normalized Base Attributes
    normalized_attributes: Dict[str, int] = field(default_factory=lambda: {
        "might": 50,      # Physical strength, brute force, load capacity
        "agility": 50,    # Speed, reflexes, dexterity, fine motor
        "vigor": 50,      # Toughness, endurance, health reservoir
        "intellect": 50,  # Logic, memory, technology, academic knowledge
        "presence": 50    # Willpower, empathy, social sway, sanity/resolve
    })
    semantic_skills: List[Dict[str, str]] = field(default_factory=list)
    tagged_inventory: List[Dict[str, Any]] = field(default_factory=list)
    tagged_traits: List[Dict[str, str]] = field(default_factory=list)
    backstory: str = ""
    roleplay_anchors: Dict[str, Any] = field(default_factory=dict)
    custom_meters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "player_name": self.player_name,
            "archetype": self.archetype,
            "level": self.level,
            "normalized_attributes": self.normalized_attributes,
            "semantic_skills": self.semantic_skills,
            "tagged_inventory": self.tagged_inventory,
            "tagged_traits": self.tagged_traits,
            "backstory": self.backstory,
            "roleplay_anchors": self.roleplay_anchors,
            "custom_meters": self.custom_meters
        }

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class BaseSheetParser(ABC):
    @abstractmethod
    def identify_system(self, form_fields: Dict[str, Any], raw_text: str) -> bool:
        """Returns True if the input matches this specific system's schema."""
        pass

    @abstractmethod
    def parse_sheet(self, pdf_stream: bytes) -> Dict[str, Any]:
        """
        Parses raw bytes into:
        {
          "universal_core": UniversalCharacterCore,
          "native_system_data": Dict[str, Any]
        }
        """
        pass

    @staticmethod
    def extract_form_fields(pdf_stream: Any) -> Tuple[Dict[str, Any], str]:
        """Extracts fillable form fields (AcroForms) and raw page text via pypdf or fallback."""
        if isinstance(pdf_stream, dict):
            return dict(pdf_stream), ""

        fields: Dict[str, Any] = {}
        text_chunks: List[str] = []

        # 1. Attempt extraction via pypdf if installed
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(pdf_stream))
            raw_fields = reader.get_fields()
            if raw_fields:
                for k, v in raw_fields.items():
                    val = v.get('/V') if isinstance(v, dict) else getattr(v, 'value', None)
                    if val is not None:
                        fields[str(k).strip()] = str(val).strip()
            if not fields:
                for page in reader.pages:
                    annots = page.get('/Annots')
                    if annots:
                        annots_list = annots.get_object() if hasattr(annots, 'get_object') else annots
                        for a in annots_list:
                            try:
                                obj = a.get_object() if hasattr(a, 'get_object') else a
                                t = obj.get('/T')
                                v = obj.get('/V')
                                if t is not None:
                                    fields[str(t).strip()] = str(v) if v is not None else ''
                            except Exception:
                                pass
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    text_chunks.append(t)
        except Exception as e:
            logger.debug(f"[PARSER] pypdf extraction fallback: {e}")

        # 2. Robust fallback via ProjectService AcroForm extractor
        if not fields:
            try:
                extracted = ProjectService.extract_pdf_form_fields(pdf_stream)
                if extracted:
                    fields.update(extracted)
            except Exception as e:
                logger.debug(f"[PARSER] ProjectService field extraction fallback error: {e}")

        # 3. Stream text extraction fallback
        if not text_chunks:
            try:
                text_matches = re.findall(rb'\(([^()]+)\)\s*Tj', pdf_stream)
                if text_matches:
                    for tm in text_matches:
                        try:
                            decoded = tm.decode('utf-8', errors='ignore')
                            if len(decoded.strip()) > 1:
                                text_chunks.append(decoded.strip())
                        except Exception:
                            pass
            except Exception:
                pass

        return fields, "\n".join(text_chunks)

