"""
Aether Desktop - Antigravity UI / Settings Populator
Populates model dropdowns and prunes raw API discovery lists.
"""

import re
from typing import List, Dict, Any, Optional

ALLOWED_MODEL_REGEX = re.compile(
    r"^(gemini-3\.8-(flash|live|live-extended-thinking)|gemini-3\.5-flash(-lite)?|gemini-3\.1-pro-preview)$"
)


def get_filtered_api_models(api_model_list) -> List[Dict[str, str]]:
    """Filters raw Gemini SDK models down to production Aether targets."""
    curated = []
    for model in api_model_list:
        raw_name = getattr(model, "name", "")
        model_id = raw_name.replace("models/", "") if isinstance(raw_name, str) else str(getattr(model, "name", model))
        if ALLOWED_MODEL_REGEX.match(model_id):
            display_name = getattr(model, "display_name", None) or model_id
            label = f"{display_name} ({model_id})" if display_name != model_id else model_id
            curated.append({
                "id": model_id,
                "label": label,
                "display_name": label
            })
    # Sort with 3.8 endpoints prioritized
    return sorted(curated, key=lambda x: ("3.8" not in x["id"], x["id"]))


def populate_dropdown(combo_box, options, default_id=None):
    """
    Populates a QComboBox with display labels while binding 
    the model/endpoint identifier into the ItemData role.
    """
    if hasattr(combo_box, "blockSignals"):
        combo_box.blockSignals(True)
    if hasattr(combo_box, "clear"):
        combo_box.clear()
    
    selected_index = 0
    for idx, opt in enumerate(options):
        label = opt.get("label") or opt.get("display_name") or opt.get("id")
        if hasattr(combo_box, "addItem"):
            combo_box.addItem(label, userData=opt["id"])
        if default_id and opt["id"] == default_id:
            selected_index = idx
            
    if hasattr(combo_box, "setCurrentIndex"):
        combo_box.setCurrentIndex(selected_index)
    if hasattr(combo_box, "blockSignals"):
        combo_box.blockSignals(False)


def refresh_aether_settings_ui(ui, config):
    models_cfg = config.get("models", {})
    
    # Tier 1 Primary Model
    if hasattr(ui, "primary_model_combo"):
        populate_dropdown(
            ui.primary_model_combo, 
            models_cfg.get("tier1_options", []), 
            default_id=config.get("primary_model_endpoint", "gemini-3.8-flash")
        )
    
    # Tier 2 Heavy Model
    if hasattr(ui, "tier2_model_combo"):
        populate_dropdown(
            ui.tier2_model_combo, 
            models_cfg.get("tier2_options", []), 
            default_id=config.get("tier2_heavy_model", "gemini-3.1-pro-preview")
        )
    
    # Speech-to-Text (STT)
    if hasattr(ui, "stt_combo"):
        populate_dropdown(
            ui.stt_combo, 
            models_cfg.get("stt_options", []), 
            default_id=config.get("stt_model_endpoint", "gemini-3.8-transcribe")
        )
    
    # Text-to-Speech (TTS)
    if hasattr(ui, "tts_combo"):
        populate_dropdown(
            ui.tts_combo, 
            models_cfg.get("tts_options", []), 
            default_id=config.get("tts_model_endpoint", "gemini-live-voice-stream")
        )
