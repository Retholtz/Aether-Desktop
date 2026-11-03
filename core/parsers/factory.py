"""
core/parsers/factory.py - Sheet Parser Ingestion Factory
Inspects incoming files and routes to the matching ruleset parser adapter.
"""
from typing import Dict, Any, List
from core.parsers.base_parser import BaseSheetParser
from core.parsers.dnd5e_parser import DnD5eParser
from core.parsers.cyberpunk_red_parser import CyberpunkRedParser
from core.parsers.coc7e_parser import CoC7eParser
from core.parsers.pf2e_parser import PF2eParser


class SheetParserFactory:
    def __init__(self):
        self.parsers: List[BaseSheetParser] = [
            DnD5eParser(),
            CyberpunkRedParser(),
            CoC7eParser(),
            PF2eParser()
        ]

    def parse_pdf(self, pdf_stream: bytes, ruleset_hint: str = None) -> Dict[str, Any]:
        fields, raw_text = BaseSheetParser.extract_form_fields(pdf_stream)

        if ruleset_hint:
            hint_low = ruleset_hint.lower()
            for p in self.parsers:
                if (hint_low in p.__class__.__name__.lower()) or (hint_low in getattr(p, "system_name", "").lower()):
                    return p.parse_sheet(pdf_stream)

        for p in self.parsers:
            if p.identify_system(fields, raw_text):
                return p.parse_sheet(pdf_stream)

        # Attempt visual Gemini Multimodal OCR identification for flat/scanned sheets
        try:
            from core.ttrpg_service import TTRPGService
            svc = TTRPGService()
            gemini_res = svc._parse_character_with_gemini("character_sheet.pdf", pdf_stream)
            if gemini_res:
                detected_r = gemini_res.get("ruleset", "").lower()
                sys_name = gemini_res.get("system_name", "").lower()
                if "cyber" in detected_r or "cyber" in sys_name:
                    return CyberpunkRedParser().parse_sheet(pdf_stream)
                elif "cthulhu" in detected_r or "cthulhu" in sys_name or "coc" in detected_r:
                    return CoC7eParser().parse_sheet(pdf_stream)
                elif "pathfinder" in detected_r or "pathfinder" in sys_name or "paizo" in sys_name:
                    return PF2eParser().parse_sheet(pdf_stream)
                elif "dnd" in detected_r or "5e" in detected_r or "d&d" in sys_name:
                    return DnD5eParser().parse_sheet(pdf_stream)
        except Exception:
            pass

        # Default fallback to D&D 5e if ambiguous
        return DnD5eParser().parse_sheet(pdf_stream)

