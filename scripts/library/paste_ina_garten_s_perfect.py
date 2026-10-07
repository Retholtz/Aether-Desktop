"""
Skill: paste_ina_garten_s_perfect
Pastes Ina Garten's Perfect Roast Chicken recipe into Google Docs using safe GUI primitives (zero ctypes).
"""

from scripts.library.paste_recipe_using_guiprimitivescontroller_and import (
    main as paste_recipe_main,
    format_windows_html_clipboard,
    set_clipboard_payload,
    wait_for_foreground_window,
    HTML_RECIPE,
    PLAIN_RECIPE,
)

if __name__ == "__main__":
    paste_recipe_main()