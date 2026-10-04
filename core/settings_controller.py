"""
Aether Desktop - Settings Controller Module
Re-exports Antigravity UI and settings dropdown helpers.
"""

from core.ui_settings import (
    ALLOWED_MODEL_REGEX,
    get_filtered_api_models,
    populate_dropdown,
    refresh_aether_settings_ui,
)

__all__ = [
    "ALLOWED_MODEL_REGEX",
    "get_filtered_api_models",
    "populate_dropdown",
    "refresh_aether_settings_ui",
]
