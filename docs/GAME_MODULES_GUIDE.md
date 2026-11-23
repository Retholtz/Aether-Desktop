# Aether Desktop — Game Modules & Extensions Guide

Aether Desktop's core is **100% game-agnostic**. Every game's keybind scanner, live telemetry parser, REST intel queries, map navigation rules, and starter macros are defined cleanly outside core Python using a two-tier plugin model:

1. **Tier 1 — Declarative `.aethermodule.json` (Zero-Code, Auto-Safe)**
   - Pure JSON. No code execution. Safe to share and import in 1 click.
   - Covers 90%+ of games: keybind auto-scanning, JSON/JSONL file telemetry, localhost HTTP telemetry, public HTTPS REST intel APIs, map search-bar routing, starter macros, and default callsigns.
2. **Tier 2 — Opt-In `.py` Community Extensions (`GameExtension`, SHA-256 Verified)**
   - Optional Python plugins for advanced integrations JSON can't express (binary shared memory like SimConnect/iRacing, UDP telemetry streams, or custom API-key sync tools).
   - **Disabled by default** until you explicitly review and click **Trust & Enable** (SHA-256 tamper-checked).

---

## Quick Start for Players (No Coding Required!)

Want a rich Aether module for a game that isn't bundled yet?

1. Open the **Games** tab in Aether Desktop and select (or `+ Add`) your game.
2. Click **`📋 Copy AI Prompt`** in the top-right module toolbar.
3. Paste the prompt into **Gemini, ChatGPT, or Claude** — it already includes Aether's exact `schema_version: 1` specification and your game's name.
4. Save the AI's JSON response as `<your_game>.aethermodule.json`.
5. Click **`📥 Import Module`** in Aether Desktop and select that file.
   - Aether automatically validates the JSON and enforces security rules (public HTTPS-only intel URLs, localhost-only HTTP telemetry).
6. Use the **Keybinds** table, **`+ Add Macro`** (with Plain English AI generation or voice creation in-game!), **`🎯 Calibrate HUD`**, and **Co-Pilot Vibe** to personalize everything visually.

---

## Where Files Live in `data/`

- `data/games/` — Your personal per-game profiles (synced keybinds, custom macros, scratchpad, callsign, Co-Pilot Vibe).
- `data/game_modules/` — User-imported or Auto-Built Tier 1 `.aethermodule.json` packs (bundled defaults live in `assets/game_modules/`).
- `data/game_extensions/` — Installed Tier 2 `.py` plugins + `trusted_extensions.json` (SHA-256 trust store).

---

## Tier 2 Community Python Extensions (Optional, For Modders)

Create a `.py` file subclassing `GameExtension` and import it via **`+ Import .py Extension`** in the **Community Python Extensions (Tier 2 — Opt-In)** card on the Games tab:

```python
from core.game_extensions import GameExtension

class MyGameExtension(GameExtension):
    module_id = "my_game"
    version = "1.0.0"
    author = "YourName"
    description = "Optional custom tools / telemetry for My Game."

    def register_tools(self, dispatcher, game_manager, engine=None):
        @dispatcher.register(
            name="my_custom_game_tool",
            description="Example custom tool registered by a Tier 2 extension.",
            parameters={"type": "object", "properties": {}},
        )
        def my_custom_game_tool():
            return {"status": "success", "message": "Hello from Tier 2!"}

        return ["my_custom_game_tool"]
```

After importing, click **Trust & Enable** to record its SHA-256 fingerprint and hot-reload its tools immediately. If the `.py` file is ever edited on disk, Aether automatically blocks loading it until you re-approve the new hash.
