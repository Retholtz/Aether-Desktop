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

    def parse_pdf(self, pdf_stream: bytes) -> Dict[str, Any]:
        fields, raw_text = BaseSheetParser.extract_form_fields(pdf_stream)

        for p in self.parsers:
            if p.identify_system(fields, raw_text):
                return p.parse_sheet(pdf_stream)

        # Default fallback to D&D 5e if ambiguous
        return DnD5eParser().parse_sheet(pdf_stream)

