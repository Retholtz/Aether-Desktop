"""
core/schema/validator.py - High-Level Validation Service
"""
from typing import Dict, Any, List, Optional, Tuple
from pydantic import ValidationError
from core.schema.character_schema import CharacterDocument
from core.logger import get_logger

logger = get_logger("SchemaValidator")


def validate_character_payload(payload: Dict[str, Any]) -> Tuple[bool, List[str], Optional[CharacterDocument]]:
    """
    Validates an ingested character dictionary against Schema 2.1 specifications.
    Returns: (is_valid, list_of_error_messages, parsed_document_or_none)
    """
    if not isinstance(payload, dict):
        return False, ["Payload must be a dictionary."], None

    try:
        doc = CharacterDocument.model_validate(payload)
        return True, [], doc
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = " -> ".join(str(p) for p in err.get("loc", []))
            msg = err.get("msg", "Validation error")
            errors.append(f"[{loc}]: {msg}")
        logger.warning("Character payload failed Schema 2.1 validation: %s", errors)
        return False, errors, None
