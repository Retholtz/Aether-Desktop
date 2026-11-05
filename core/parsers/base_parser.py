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


class UniversalCharacterCore(dict):
    """
    Normalized core roleplay data model across genres and systems.
    Subclasses dict to support direct JSON serialization, dict indexing, and dot attribute access.
    """
    def __init__(
        self,
        name: str,
        player_name: str,
        archetype: str,
        level: int,
        normalized_attributes: Optional[Dict[str, int]] = None,
        semantic_skills: Optional[List[Dict[str, str]]] = None,
        tagged_inventory: Optional[List[Dict[str, Any]]] = None,
        tagged_traits: Optional[List[Dict[str, str]]] = None,
        backstory: str = "",
        roleplay_anchors: Optional[Dict[str, Any]] = None,
        custom_meters: Optional[Dict[str, Any]] = None,
        description: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        super().__init__(
            name=name,
            player_name=player_name,
            archetype=archetype,
            level=level,
            normalized_attributes=normalized_attributes or {
                "might": 50, "agility": 50, "vigor": 50, "intellect": 50, "presence": 50
            },
            semantic_skills=semantic_skills or [],
            tagged_inventory=tagged_inventory or [],
            tagged_traits=tagged_traits or [],
            backstory=backstory,
            roleplay_anchors=roleplay_anchors or {},
            custom_meters=custom_meters or {},
            description=description or {},
            **kwargs
        )

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError:
            raise AttributeError(f"'UniversalCharacterCore' object has no attribute '{item}'")

    def __setattr__(self, key: str, value: Any):
        self[key] = value

    def to_dict(self) -> Dict[str, Any]:
        return dict(self)


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

