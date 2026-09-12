"""
Aether Desktop - Central Tool Router & Execution Dispatcher
Maps Gemini Multimodal Live tool calls directly to their respective execution tiers,
enforcing complete exception isolation so tool faults cannot block or crash the WebSocket/audio pipeline.
"""

import asyncio
import json
import os
import re
import time
from typing import Callable, Dict, List, Optional

from core.logger import get_logger
from google import genai
from google.genai import types

logger = get_logger("Dispatcher")

from core.screen_stream import ScreenCapturePipeline
from security.crypto import unprotect_secret
from security.whitelist import WhitelistValidator
from tools.os_controls import (
    maximize_window,
    minimize_window,
    restore_window,
    focus_window,
    close_window,
    navigate_browser,
)
from tools.gui_primitives import GuiPrimitivesController
from tools.script_runner import ScriptRunner
from tools.skill_library import SkillLibrary
from core.user_memory import UserMemory
from tools.payload_sanitizer import sanitize_tool_result



# Gemini Live Tool Declarations for Desktop Automation
MAXIMIZE_WINDOW_DECLARATION = {
    "name": "maximize_window",
    "description": (
        "Natively maximizes an open application window (e.g. 'Chrome', 'Notepad', 'Word', 'Excel', 'Spotify') "
        "using direct Win32 API handles (HWND). Always call this instead of simulating mouse clicks on the window title bar or maximize icon."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "The name or title of the window to maximize (e.g. 'chrome', 'google chrome', 'notepad', 'word', 'excel')."
            }
        },
        "required": ["app_name"]
    }
}

MINIMIZE_WINDOW_DECLARATION = {
    "name": "minimize_window",
    "description": "Natively minimizes an open application window using direct Win32 API handles (HWND).",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "The name or title of the window to minimize (e.g. 'chrome', 'notepad', 'spotify')."
            }
        },
        "required": ["app_name"]
    }
}

RESTORE_WINDOW_DECLARATION = {
    "name": "restore_window",
    "description": "Natively restores an application window to its normal (non-maximized, non-minimized) size using Win32 API handles.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "The name or title of the window to restore."
            }
        },
        "required": ["app_name"]
    }
}

FOCUS_WINDOW_DECLARATION = {
    "name": "focus_window",
    "description": (
        "Brings a specific open application window (e.g. 'Notepad', 'Chrome', 'Word', 'Excel', 'Spotify') "
        "to the foreground using native Win32 handles so it receives keyboard focus and user input."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "The name or title of the window to focus (e.g. 'notepad', 'chrome', 'word', 'excel')."
            }
        },
        "required": ["app_name"]
    }
}

LAUNCH_APPLICATION_DECLARATION = {
    "name": "launch_application",
    "description": (
        "Launches an installed Windows desktop application (e.g. Google Chrome, Notepad, Word, "
        "Excel, Calculator, Spotify, Blender, File Explorer) if permitted by the user's security whitelist. "
        "Can optionally open a target URL, file, or search query. Supports user profile selection for browsers (e.g. profile='Michael')."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "The name or executable of the application to launch (e.g. 'chrome', 'notepad', 'calc', 'winword', 'excel', 'spotify', 'blender', 'explorer')."
            },
            "target": {
                "type": "STRING",
                "description": "Optional URL, search query, or file path to open with the application (e.g. 'https://www.google.com/search?q=News+today' or 'document.txt')."
            },
            "profile": {
                "type": "STRING",
                "description": "Optional user profile name for browsers (e.g. 'Michael', 'Default', 'Profile 1'). When launching Chrome with profile='Michael', it opens directly under Michael's profile without the profile picker."
            }
        },
        "required": ["app_name"]
    }
}

CLOSE_APPLICATION_DECLARATION = {
    "name": "close_application",
    "description": "Closes or terminates a running Windows desktop application if permitted by the user's security whitelist.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "The name or executable of the program to close (e.g. 'chrome', 'notepad', 'calc', 'winword', 'excel', 'spotify')."
            }
        },
        "required": ["app_name"]
    }
}

ADD_TO_WHITELIST_DECLARATION = {
    "name": "add_to_whitelist",
    "description": (
        "Adds an application (e.g. explorer.exe, calc.exe, steam.exe, discord.exe) to the user's security whitelist "
        "so that it can be launched. ALWAYS call this when an application was blocked by the whitelist and the user gives "
        "verbal permission (e.g. 'yes', 'add it', 'sure', 'go ahead', 'please do') to add it. "
        "After adding it to the whitelist, immediately launch the requested application to complete the user's request."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "The name or executable of the application to add to the whitelist (e.g. 'explorer', 'calc', 'steam'). If the user simply said 'yes' or 'add it', pass the name of the blocked application or 'last_blocked'."
            }
        },
        "required": ["app_name"]
    }
}

REMOVE_FROM_WHITELIST_DECLARATION = {
    "name": "remove_from_whitelist",
    "description": "Removes an application from the user's security whitelist.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "The name or executable of the application to remove from the whitelist."
            }
        },
        "required": ["app_name"]
    }
}

FIND_AND_CLICK_ELEMENT_DECLARATION = {
    "name": "find_and_click_element",
    "description": (
        "Uses high-precision visual grounding AI to locate and click on any visual element, webpage link, "
        "button, icon, input field, or text label on the screen or inside an application. "
        "ALWAYS use this tool when asked to click a link (e.g. 'Click on the first link', 'Click the Wikipedia link'), "
        "click a button (e.g. 'Click Submit', 'Click Sign In', 'Click Search', 'Close popup'), or click any named UI element on screen."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "target_description": {
                "type": "STRING",
                "description": "The visual element, link, or text to find and click (e.g. 'first link in search results', 'Wikipedia link', 'Search button', 'Restore button', 'Settings icon')."
            },
            "app_name": {
                "type": "STRING",
                "description": "Optional application window to focus and inspect (e.g. 'chrome', 'notepad')."
            },
            "button": {
                "type": "STRING",
                "description": "Mouse button: 'left', 'right', or 'middle'. Default is 'left'."
            },
            "clicks": {
                "type": "INTEGER",
                "description": "Number of clicks: 1 for single click, 2 for double click. Default is 1."
            }
        },
        "required": ["target_description"]
    }
}

MOUSE_CLICK_DECLARATION = {
    "name": "mouse_click",
    "description": (
        "Clicks on a specific coordinate on the desktop screen. "
        "Use normalized coordinates from 0 to 1000 based on what you see in the desktop vision stream "
        "(0,0 is top-left of the screen, 1000,1000 is bottom-right of the screen). "
        "Pass app_name to bring the target application to the foreground before clicking."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "x": {
                "type": "NUMBER",
                "description": "The X coordinate (0-1000 normalized across the screen width)."
            },
            "y": {
                "type": "NUMBER",
                "description": "The Y coordinate (0-1000 normalized across the screen height)."
            },
            "button": {
                "type": "STRING",
                "description": "Mouse button: 'left', 'right', or 'middle'. Default is 'left'."
            },
            "clicks": {
                "type": "INTEGER",
                "description": "Number of clicks: 1 for single click, 2 for double click. Default is 1."
            },
            "monitor": {
                "type": "STRING",
                "description": "Target monitor: 'auto' (active monitor), '1' (primary), '2' (secondary), or 'all'. Default is 'auto'."
            },
            "app_name": {
                "type": "STRING",
                "description": "Optional name of the target application window (e.g. 'chrome', 'notepad') to focus before clicking."
            }
        },
        "required": ["x", "y"]
    }
}


TYPE_TEXT_DECLARATION = {
    "name": "type_text",
    "description": (
        "Enters text into the active focused field or specified application window (e.g. 'notepad', 'word', 'chrome'). "
        "When app_name is provided, automatically verifies and brings the target window to the foreground before typing. "
        "Supports all Unicode characters, symbols, and multiline text. "
        "Do NOT use this to navigate or search the web in a browser (use navigate_browser instead). "
        "Do NOT use this to generate tables in Google Docs or Word (use run_saved_script with 'create_table_google_docs' instead)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "text": {
                "type": "STRING",
                "description": "The text string to type."
            },
            "press_enter": {
                "type": "BOOLEAN",
                "description": "Whether to press the Enter key after typing (e.g. to submit a search or form). Default is false."
            },
            "app_name": {
                "type": "STRING",
                "description": "Optional application to bring to focus before typing (e.g. 'notepad', 'word', 'chrome', 'excel'). If omitted, types into the currently active or last launched window."
            }
        },
        "required": ["text"]
    }
}

PRESS_KEY_DECLARATION = {
    "name": "press_key",
    "description": (
        "Presses a keyboard key or hotkey shortcut on the desktop (e.g. 'enter', 'tab', 'escape', 'ctrl+t', 'ctrl+l', 'ctrl+w', 'alt+f4', 'backspace')."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "key_combo": {
                "type": "STRING",
                "description": "The key or key combination to press (e.g. 'enter', 'tab', 'ctrl+t', 'ctrl+l', 'escape', 'backspace')."
            }
        },
        "required": ["key_combo"]
    }
}

SCROLL_PAGE_DECLARATION = {
    "name": "scroll_page",
    "description": "Scrolls the currently active window or page up or down.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "direction": {
                "type": "STRING",
                "description": "Direction to scroll: 'down' or 'up'. Default is 'down'."
            },
            "amount": {
                "type": "INTEGER",
                "description": "Number of scroll steps. Default is 3."
            }
        },
        "required": []
    }
}

NAVIGATE_BROWSER_DECLARATION = {
    "name": "navigate_browser",
    "description": (
        "Deterministically navigates an open browser (or launches it if not open) to a URL or performs a web search. "
        "Brings the browser window to focus, uses Ctrl+L to activate the address/search bar (omnibox), "
        "and submits the query or URL. Always call this when asked to search or open a website in an already open browser "
        "(e.g. 'search for News today', 'open MSN.com', 'go to youtube.com') instead of guessing mouse coordinates for the search bar."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query_or_url": {
                "type": "STRING",
                "description": "The website URL to navigate to (e.g. 'https://www.msn.com', 'msn.com', 'youtube.com') or search phrase (e.g. 'News today')."
            },
            "app_name": {
                "type": "STRING",
                "description": "The browser application name. Default is 'chrome'."
            }
        },
        "required": ["query_or_url"]
    }
}

CAPTURE_SCREEN_SNAPSHOT_DECLARATION = {
    "name": "capture_screen_snapshot",
    "description": (
        "Captures an immediate, high-resolution snapshot of a specific monitor or the whole desktop to inspect details or read fine text. "
        "Monitor options: 'auto' (active window's monitor), 'primary', 'secondary', or 'all' (all displays combined)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "monitor": {
                "type": "STRING",
                "description": "The monitor to capture: 'auto', 'primary', 'secondary', or 'all'. Default is 'auto'."
            }
        },
        "required": []
    }
}

RUN_SAVED_SCRIPT_DECLARATION = {
    "name": "run_saved_script",
    "description": (
        "Executes a tested automation routine from the permanent Skill Library by name (e.g. 'create_table_google_docs', "
        "'create_table_word', 'tile_windows') with optional arguments. ALWAYS check if a matching saved skill exists in your "
        "library before writing new code from scratch!"
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "skill_name": {
                "type": "STRING",
                "description": "The exact name of the saved skill (e.g. 'create_table_google_docs', 'create_table_word', 'tile_windows')."
            },
            "args": {
                "type": "OBJECT",
                "description": "Optional dictionary of arguments matching the skill's parameter schema (e.g. {'headers': ['A', 'B'], 'rows': [['1', '2']]})."
            }
        },
        "required": ["skill_name"]
    }
}

SAVE_SCRIPT_TO_LIBRARY_DECLARATION = {
    "name": "save_script_to_library",
    "description": (
        "Saves a newly generated, tested Python automation script into the permanent Skill Library so it can be reused later "
        "by name without regenerating code. AST safety checks are verified before saving to the repository."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "skill_name": {
                "type": "STRING",
                "description": "Unique snake_case identifier for the skill (e.g. 'format_sales_sheet', 'backup_workspace', 'tile_three_windows')."
            },
            "description": {
                "type": "STRING",
                "description": "Clear plain-English summary of what this automation skill does and what problems it solves."
            },
            "script_code": {
                "type": "STRING",
                "description": "The complete Python code for the skill. Must read arguments from SKILL_ARGS or sys.argv if parameterized."
            },
            "parameters": {
                "type": "OBJECT",
                "description": "Optional parameter specification documenting the expected argument names and descriptions."
            }
        },
        "required": ["skill_name", "description", "script_code"]
    }
}

LIST_SAVED_SKILLS_DECLARATION = {
    "name": "list_saved_skills",
    "description": (
        "Lists or searches available reusable automation skills currently in the permanent Skill Library, including descriptions and required parameters."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "Optional keyword or task query to search for relevant skills (e.g. 'table', 'word', 'tile windows')."
            }
        },
        "required": []
    }
}

LIST_AVAILABLE_SKILLS_DECLARATION = {
    "name": "list_available_skills",
    "description": (
        "Searches and lists reusable automation skills in the permanent Skill Library by keyword or intent query."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "Optional keyword or task query to search for relevant skills (e.g. 'table', 'word', 'tile windows')."
            }
        },
        "required": []
    }
}

READ_SAVED_SKILL_DECLARATION = {
    "name": "read_saved_skill",
    "description": (
        "Inspects and returns the full source code and documentation of a saved skill from the permanent Skill Library. "
        "Use this tool whenever you want to see how a saved skill was implemented (e.g. how it formats tables, images, or documents) "
        "instead of running scripts to inspect files."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "skill_name": {
                "type": "STRING",
                "description": "The exact identifier name of the skill to inspect (e.g. 'create_table_google_docs', 'insert_dss_heatmap_heading_and')."
            }
        },
        "required": ["skill_name"]
    }
}

REMEMBER_USER_FACT_DECLARATION = {
    "name": "remember_user_fact",
    "description": (
        "Stores or updates a persistent personal fact about the user (e.g. spouse, family members, birthdays, anniversaries, career, hobbies, vehicles) "
        "into the user's permanent profile. Call this whenever the user asks you to remember something or reveals personal facts. "
        "For calendar dates (birthdays, anniversaries), always convert to 'YYYY-MM-DD' (e.g. '1977-05-01') under category 'dates' with data_type='date'. "
        "If the user shares multiple facts at once (e.g. wife's name and birthday), call remember_user_fact for each fact."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "category": {
                "type": "STRING",
                "description": "Category: 'family', 'dates', 'work', 'interests', 'devices', or 'general'."
            },
            "key": {
                "type": "STRING",
                "description": "Unique key (e.g. 'wife_name', 'wife_birthday', 'employer', 'favorite_hobby')."
            },
            "value": {
                "type": "STRING",
                "description": "Value to remember (e.g. 'Traci', '1977-05-01', 'Acme Corp', 'Photography')."
            },
            "data_type": {
                "type": "STRING",
                "description": "Data type: 'string', 'date' (format YYYY-MM-DD), 'number', or 'json'. Default is 'string'."
            }
        },
        "required": ["category", "key", "value"]
    }
}

FORGET_USER_FACT_DECLARATION = {
    "name": "forget_user_fact",
    "description": "Removes a specific fact from the user's persistent profile by category and key.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "category": {
                "type": "STRING",
                "description": "Category of the fact to remove."
            },
            "key": {
                "type": "STRING",
                "description": "Key of the fact to remove."
            }
        },
        "required": ["category", "key"]
    }
}

GET_USER_PROFILE_DECLARATION = {
    "name": "get_user_profile",
    "description": "Retrieves active stored facts about the user from their persistent profile, optionally filtered by category.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "category": {
                "type": "STRING",
                "description": "Optional category filter: 'family', 'dates', 'work', 'interests', 'devices', or 'general'."
            }
        },
        "required": []
    }
}

QUERY_USER_MEMORY_DECLARATION = {
    "name": "query_user_memory",
    "description": (
        "Queries the local persistent user knowledge database for facts, preferences, dates, family details, "
        "or project context matching a search keyword. Call this on-demand when the user refers to personal context, "
        "family members, or past setups not currently present in your active conversation."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "search_term": {
                "type": "STRING",
                "description": "The search keyword or term to look up in user memory (e.g., 'birthday', 'wife', 'gpu', 'coffee', 'project')."
            },
            "category": {
                "type": "STRING",
                "description": "Optional category filter (e.g., 'dates', 'family', 'preferences', 'hardware', 'work', 'general')."
            }
        },
        "required": ["search_term"]
    }
}

EXECUTE_AUTOMATION_SCRIPT_DECLARATION = {
    "name": "execute_automation_script",
    "description": (
        "Universal dynamic code execution engine. Generates and executes an on-the-fly Python script to solve novel or complex desktop tasks. "
        "Use this for document creation, table generation, data analysis (pandas), Microsoft Office COM (Word/Excel via win32com.client), "
        "file batching, complex calculations, and window layouts. "
        "If a script fails with an exception, the full traceback is returned so you can diagnose the issue, adjust your code, and retry. "
        "When you solve a novel task with a reusable script, save it to the permanent library using `save_script_to_library`."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "script_code": {
                "type": "STRING",
                "description": "The executable Python or PowerShell code."
            },
            "script_type": {
                "type": "STRING",
                "description": "The script language: 'python' or 'powershell'. Default is 'python'."
            },
            "description": {
                "type": "STRING",
                "description": "A clear 1-line description of what this automation script does."
            }
        },
        "required": ["script_code"]
    }
}


def get_all_tool_declarations() -> List[dict]:
    """Returns the full list of tool declarations for Gemini Multimodal Live."""
    return [
        MAXIMIZE_WINDOW_DECLARATION,
        MINIMIZE_WINDOW_DECLARATION,
        RESTORE_WINDOW_DECLARATION,
        FOCUS_WINDOW_DECLARATION,
        LAUNCH_APPLICATION_DECLARATION,
        CLOSE_APPLICATION_DECLARATION,
        ADD_TO_WHITELIST_DECLARATION,
        REMOVE_FROM_WHITELIST_DECLARATION,
        NAVIGATE_BROWSER_DECLARATION,
        FIND_AND_CLICK_ELEMENT_DECLARATION,
        MOUSE_CLICK_DECLARATION,
        TYPE_TEXT_DECLARATION,
        PRESS_KEY_DECLARATION,
        SCROLL_PAGE_DECLARATION,
        CAPTURE_SCREEN_SNAPSHOT_DECLARATION,
        RUN_SAVED_SCRIPT_DECLARATION,
        SAVE_SCRIPT_TO_LIBRARY_DECLARATION,
        LIST_SAVED_SKILLS_DECLARATION,
        LIST_AVAILABLE_SKILLS_DECLARATION,
        READ_SAVED_SKILL_DECLARATION,
        REMEMBER_USER_FACT_DECLARATION,
        FORGET_USER_FACT_DECLARATION,
        GET_USER_PROFILE_DECLARATION,
        QUERY_USER_MEMORY_DECLARATION,
        EXECUTE_AUTOMATION_SCRIPT_DECLARATION,
    ]


class ToolDispatcher:
    """
    Central dispatcher routing Gemini function calls to Tier 1 (Win32 OS),
    Tier 2 (GUI Primitives), Tier 3 (Script Runner), and Security Validator.
    """

    def __init__(
        self,
        screen_pipeline: Optional[ScreenCapturePipeline] = None,
        on_event: Optional[Callable[[str, dict], None]] = None,
        whitelist_getter: Optional[Callable[[], List[str]]] = None,
        whitelist_updater: Optional[Callable[[List[str]], dict]] = None,
        config_getter: Optional[Callable[[], dict]] = None,
        genai_client = None,
    ):
        self.screen_pipeline = screen_pipeline or ScreenCapturePipeline()
        self.gui_controller = GuiPrimitivesController(self.screen_pipeline)
        self.script_runner = ScriptRunner()
        self.skill_library = SkillLibrary()
        self.user_memory = UserMemory()
        self.on_event = on_event
        self.whitelist_getter = whitelist_getter or (lambda: [])
        self.whitelist_updater = whitelist_updater
        self.config_getter = config_getter or (lambda: {})
        self.genai_client = genai_client
        self.last_blocked_app = ""
        self.last_target_app = ""


    def notify(self, event_type: str, data: dict):
        if self.on_event:
            try:
                self.on_event(event_type, data)
            except Exception as e:
                print(f"[DISPATCHER NOTIFY ERROR] {e}")

    async def find_and_click_element(
        self,
        target_description: str,
        app_name: Optional[str] = None,
        button: str = "left",
        clicks: int = 1
    ) -> dict:
        """
        Takes a crisp high-resolution snapshot, queries visual grounding AI
        to detect the exact 2D bounding box for target_description,
        and clicks dead-center on the target.
        """
        clean_target = str(target_description).strip()
        if not clean_target:
            return {"status": "error", "message": "target_description cannot be empty."}

        # 1. Focus application window if specified
        if app_name:
            focus_window(app_name)
            await asyncio.sleep(0.12)

        # 2. Capture crisp snapshot (1024 max_dim provides clear text while keeping latency fast)
        jpeg_bytes, meta = await self.screen_pipeline.capture_frame(target="auto", max_dim=1024, quality=75)
        mon_rect = meta.get("monitor_rect", {"left": 0, "top": 0, "width": 2560, "height": 1600})

        # 3. Determine visual grounding model from config
        cfg = self.config_getter() if self.config_getter else {}
        v_endpoint = cfg.get("vision", {}).get("endpoint", "gemini-3.8-flash-snapshot")
        if "pro" in str(v_endpoint).lower():
            grounding_model = "gemini-3.1-pro-preview"
        else:
            grounding_model = "gemini-3.8-flash"

        # 4. Resolve GenAI client
        client = self.genai_client
        if not client:
            encrypted_key = cfg.get("api", {}).get("api_key_encrypted", "")
            api_key = unprotect_secret(encrypted_key) if encrypted_key else os.environ.get("GEMINI_API_KEY", "")
            if not api_key:
                return {"status": "error", "message": "Gemini API key is not configured."}
            client = genai.Client(api_key=api_key)

        prompt = (
            f"Locate the 2D bounding box of: '{clean_target}'.\n"
            "Return a JSON list with 'box_2d' in [ymin, xmin, ymax, xmax] normalized from 0 to 1000 "
            "relative to the image boundaries (0 is top/left, 1000 is bottom/right).\n"
            "Format: [{\"box_2d\": [ymin, xmin, ymax, xmax], \"label\": \"description\"}]"
        )

        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=grounding_model,
                contents=[
                    types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
                    prompt
                ]
            )
            resp_text = response.text or ""
        except Exception as e:
            return {"status": "error", "message": f"Visual grounding failed with {grounding_model}: {e}"}

        # 5. Extract bounding box
        box = None
        m = re.search(r"\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]", resp_text)
        if m:
            box = [int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))]
        else:
            return {
                "status": "not_found",
                "message": f"Could not find visual element '{clean_target}' on screen.",
                "model_response": resp_text
            }

        ymin, xmin, ymax, xmax = box
        center_x_norm = (xmin + xmax) / 2.0
        center_y_norm = (ymin + ymax) / 2.0

        # 6. Click at center coordinates
        click_res = self.gui_controller.click(
            x=center_x_norm,
            y=center_y_norm,
            button=button,
            clicks=clicks,
            monitor="auto",
            normalized=True,
            app_name=app_name,
            window_relative=False
        )

        phys_x, phys_y = click_res.get("coords", (int(center_x_norm), int(center_y_norm)))
        msg = f"Located '{clean_target}' at screen ({phys_x}, {phys_y}) (box: {box}) via {grounding_model} and clicked."
        self.notify("chat_event", {
            "type": "tool",
            "name": "Precision Vision",
            "content": f"🎯 [CLICK TARGET] '{clean_target}' -> ({phys_x}, {phys_y}) via {grounding_model}"
        })
        return {
            "status": "success",
            "target": clean_target,
            "box_2d": box,
            "coords": (phys_x, phys_y),
            "model_used": grounding_model,
            "message": msg
        }

    async def dispatch(self, fn_name: str, fn_args: dict) -> dict:
        """
        Executes the named function with the given arguments.
        Enforces complete exception isolation, latency benchmarking, and diagnostic logging.
        """
        args = fn_args or {}
        t0 = time.perf_counter()
        args_repr = json.dumps(args, default=str)
        if len(args_repr) > 250:
            args_repr = args_repr[:250] + "... [truncated]"
        logger.info(f"[TOOL CALL] {fn_name} | Args: {args_repr}")

        try:
            result = await self._dispatch_internal(fn_name, args)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            st = result.get("status", "success") if isinstance(result, dict) else "success"
            logger.info(f"[TOOL RESULT] {fn_name} | Duration: {elapsed_ms:.1f}ms | Status: {st}")
            return sanitize_tool_result(result)
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            err_msg = f"Tool execution failed for '{fn_name}': {str(e)}"
            logger.error(f"[TOOL ERROR] {fn_name} failed after {elapsed_ms:.1f}ms: {err_msg}", exc_info=True)
            self.notify("chat_event", {
                "type": "error",
                "content": err_msg
            })
            return sanitize_tool_result({
                "status": "error",
                "error": str(e),
                "message": err_msg
            })

    async def _dispatch_internal(self, fn_name: str, args: dict) -> dict:
        try:
            # -------------------------------------------------------------
            # Tier 1: Win32 Native Window Management
            # -------------------------------------------------------------
            if fn_name == "maximize_window":
                app_name = str(args.get("app_name", "")).strip()
                if app_name:
                    self.last_target_app = app_name
                result = maximize_window(app_name)
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Window Control",
                    "content": f"🪟 [MAXIMIZE] {result.get('message', app_name)}"
                })
                return result

            elif fn_name == "minimize_window":
                app_name = str(args.get("app_name", "")).strip()
                result = minimize_window(app_name)
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Window Control",
                    "content": f"🪟 [MINIMIZE] {result.get('message', app_name)}"
                })
                return result

            elif fn_name == "restore_window":
                app_name = str(args.get("app_name", "")).strip()
                if app_name:
                    self.last_target_app = app_name
                result = restore_window(app_name)
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Window Control",
                    "content": f"🪟 [RESTORE] {result.get('message', app_name)}"
                })
                return result

            elif fn_name == "focus_window":
                app_name = str(args.get("app_name") or args.get("window_name") or "").strip()
                if app_name:
                    self.last_target_app = app_name
                result = focus_window(app_name)
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Window Control",
                    "content": f"🪟 [FOCUS] {result.get('message', app_name)}"
                })
                return result

            # -------------------------------------------------------------
            # Tier 1 + Security: App Launching & Termination
            # -------------------------------------------------------------
            elif fn_name == "launch_application":
                app_name = str(args.get("app_name", "")).strip()
                if app_name:
                    self.last_target_app = app_name
                target = args.get("target", None)
                profile = args.get("profile", None)
                whitelist = self.whitelist_getter()
                result = WhitelistValidator.validate_and_launch(app_name, whitelist, target=target, profile=profile)
                if result.get("status") == "success":
                    prof_info = f" [Profile: {result.get('profile')}]" if result.get("profile") else ""
                    self.notify("chat_event", {
                        "type": "tool",
                        "name": "Desktop Hook",
                        "content": f"🚀 [LAUNCHED] {result.get('executable', app_name)}{prof_info}" + (f" -> '{target}'" if target else "")
                    })
                else:
                    self.last_blocked_app = result.get("executable") or app_name
                    self.notify("chat_event", {
                        "type": "tool",
                        "name": "Desktop Hook",
                        "content": f"🛡️ [BLOCKED] {result.get('error', 'Failed to launch application.')}"
                    })
                return result

            elif fn_name == "close_application":
                app_name = str(args.get("app_name", "")).strip()
                whitelist = self.whitelist_getter()
                result = WhitelistValidator.validate_and_close(app_name, whitelist)
                if result.get("status") == "success":
                    self.notify("chat_event", {
                        "type": "tool",
                        "name": "Desktop Hook",
                        "content": f"🛑 [CLOSED] {result.get('executable', app_name)}"
                    })
                else:
                    if result.get("status") == "blocked":
                        self.last_blocked_app = result.get("executable") or app_name
                    self.notify("chat_event", {
                        "type": "tool",
                        "name": "Desktop Hook",
                        "content": f"⚠️ [WARNING] {result.get('error', result.get('message', 'Failed to close application.'))}"
                    })
                return result

            # -------------------------------------------------------------
            # Security Whitelist Management
            # -------------------------------------------------------------
            elif fn_name == "add_to_whitelist":
                app_name = str(args.get("app_name", "")).strip()
                generic_aliases = ("it", "that", "this", "the app", "the program", "last_blocked", "blocked_app", "yes", "add it", "sure", "please do", "go ahead")
                if (not app_name or app_name.lower() in generic_aliases) and self.last_blocked_app:
                    app_name = self.last_blocked_app

                whitelist = self.whitelist_getter()
                result = WhitelistValidator.add_to_whitelist(app_name, whitelist)
                if result.get("status") in ("success", "already_allowed"):
                    updated_whitelist = result.get("whitelist", whitelist)
                    if self.whitelist_updater:
                        self.whitelist_updater(updated_whitelist)
                    self.notify("chat_event", {
                        "type": "tool",
                        "name": "Security Hook",
                        "content": f"🛡️ [WHITELIST] Added '{result.get('executable', app_name)}' to allowed applications."
                    })
                else:
                    self.notify("chat_event", {
                        "type": "tool",
                        "name": "Security Hook",
                        "content": f"🛡️ [WHITELIST] {result.get('message', 'Failed to add application.')}"
                    })
                return result

            elif fn_name == "remove_from_whitelist":
                app_name = str(args.get("app_name", "")).strip()
                whitelist = self.whitelist_getter()
                result = WhitelistValidator.remove_from_whitelist(app_name, whitelist)
                if result.get("status") == "success":
                    updated_whitelist = result.get("whitelist", whitelist)
                    if self.whitelist_updater:
                        self.whitelist_updater(updated_whitelist)
                    self.notify("chat_event", {
                        "type": "tool",
                        "name": "Security Hook",
                        "content": f"🛡️ [WHITELIST] Removed '{result.get('executable', app_name)}' from allowed applications."
                    })
                else:
                    self.notify("chat_event", {
                        "type": "tool",
                        "name": "Security Hook",
                        "content": f"🛡️ [WHITELIST] {result.get('message', 'Failed to remove application.')}"
                    })
                return result

            elif fn_name == "navigate_browser":
                query_or_url = str(args.get("query_or_url", "")).strip()
                app_name = str(args.get("app_name", "chrome")).strip() or "chrome"
                if app_name:
                    self.last_target_app = app_name
                whitelist = self.whitelist_getter()
                result = navigate_browser(query_or_url, app_name=app_name, whitelist=whitelist)
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Browser Control",
                    "content": f"🌐 [NAVIGATE] {app_name}: {result.get('message', query_or_url)}"
                })
                return result

            # -------------------------------------------------------------
            # Tier 2: Low-Level GUI Primitives & Precision Vision Grounding
            # -------------------------------------------------------------
            elif fn_name == "find_and_click_element":
                target_description = str(args.get("target_description", "")).strip()
                app_name = args.get("app_name", None)
                if app_name:
                    self.last_target_app = app_name
                button = str(args.get("button", "left"))
                clicks = int(args.get("clicks", 1))
                return await self.find_and_click_element(
                    target_description=target_description,
                    app_name=app_name,
                    button=button,
                    clicks=clicks
                )

            elif fn_name == "mouse_click":

                x = float(args.get("x", 500))
                y = float(args.get("y", 500))
                button = str(args.get("button", "left"))
                clicks = int(args.get("clicks", 1))
                monitor = args.get("monitor", "auto")
                app_name = args.get("app_name", None)
                if app_name:
                    self.last_target_app = app_name
                result = self.gui_controller.click(x=x, y=y, button=button, clicks=clicks, monitor=monitor, normalized=True, app_name=app_name)
                target_desc = f"window '{app_name}'" if app_name else f"Monitor {result.get('monitor', 1)}"
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Desktop Control",
                    "content": f"🖱️ [CLICK] {button.title()} click at ({int(x)}, {int(y)}) on {target_desc}"
                })
                return result

            elif fn_name == "type_text":
                text = str(args.get("text", ""))
                press_enter = bool(args.get("press_enter", False))
                app_name = args.get("app_name", None) or self.last_target_app or None
                if app_name:
                    self.last_target_app = app_name
                result = self.gui_controller.type_text(text=text, press_enter=press_enter, app_name=app_name)
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Desktop Control",
                    "content": f"⌨️ [TYPE] \"{text}\"" + (f" in {app_name}" if app_name else "") + (" [ENTER]" if press_enter else "")
                })
                return result

            elif fn_name == "press_key":
                key_combo = str(args.get("key_combo", ""))
                result = self.gui_controller.press_key(key_combo=key_combo)
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Desktop Control",
                    "content": f"⌨️ [KEY] {key_combo}"
                })
                return result

            elif fn_name == "scroll_page":
                direction = str(args.get("direction", "down"))
                amount = int(args.get("amount", 3))
                result = self.gui_controller.scroll(amount=amount, direction=direction)
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Desktop Control",
                    "content": f"📜 [SCROLL] {direction} ({amount} steps)"
                })
                return result

            elif fn_name == "capture_screen_snapshot":
                monitor = args.get("monitor", "auto")
                jpeg_bytes, meta = await self.screen_pipeline.capture_frame(target=monitor, max_dim=1024, quality=75)
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Vision Hook",
                    "content": f"📷 [SNAPSHOT] Captured Monitor {meta.get('monitor_index', 0)} ({meta.get('original_size', '')})"
                })
                return {
                    "status": "success",
                    "monitor": meta.get("monitor_index"),
                    "details": meta,
                    "_jpeg_bytes": jpeg_bytes
                }

            # -------------------------------------------------------------
            # Tier 3: Sandboxed Script Runner & Skill Library
            # -------------------------------------------------------------
            elif fn_name == "run_saved_script":
                skill_name = str(args.get("skill_name", "")).strip()
                skill_args = args.get("args") or {}
                result = await asyncio.to_thread(
                    self.skill_library.run_skill,
                    skill_name=skill_name,
                    args=skill_args
                )
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Skill Library",
                    "content": f"⚡ [SKILL: {skill_name}] {result.get('message', 'Executed.')}"
                })
                return result

            elif fn_name == "save_script_to_library":
                skill_name = str(args.get("skill_name", "")).strip()
                description = str(args.get("description", "")).strip()
                script_code = str(args.get("script_code", "")).strip()
                parameters = args.get("parameters") or {}
                result = await asyncio.to_thread(
                    self.skill_library.save_skill,
                    skill_name=skill_name,
                    description=description,
                    script_code=script_code,
                    parameters=parameters
                )
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Skill Library",
                    "content": f"💾 [SAVED SKILL] {result.get('message', skill_name)}"
                })
                return result

            elif fn_name in ("list_saved_skills", "list_available_skills"):
                query = str(args.get("query", "")).strip()
                if query:
                    matched = self.skill_library.find_relevant_skills(query, top_k=5)
                    summary = self.skill_library.get_manifest_summary(query=query)
                else:
                    matched = self.skill_library.get_skills_manifest()
                    summary = self.skill_library.get_manifest_summary()
                return {
                    "status": "success",
                    "skills": matched,
                    "count": len(matched),
                    "total_library_skills": len(self.skill_library.get_skills_manifest()),
                    "summary": summary
                }

            elif fn_name == "read_saved_skill":
                skill_name = str(args.get("skill_name", "")).strip()
                code = self.skill_library.get_skill_code(skill_name)
                if code is not None:
                    return {
                        "status": "success",
                        "skill_name": skill_name,
                        "code": code,
                        "message": f"Successfully retrieved source code for skill '{skill_name}'."
                    }
                else:
                    return {
                        "status": "error",
                        "message": f"Skill '{skill_name}' not found in permanent library."
                    }

            elif fn_name == "remember_user_fact":
                cat = str(args.get("category", "general")).strip()
                key = str(args.get("key", "")).strip()
                val = str(args.get("value", "")).strip()
                dtype = str(args.get("data_type", "string")).strip()
                result = self.user_memory.remember_fact(cat, key, val, dtype)
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "User Memory",
                    "content": f"🧠 [REMEMBERED] [{cat}] {key} = {val}"
                })
                return result

            elif fn_name == "forget_user_fact":
                cat = str(args.get("category", "general")).strip()
                key = str(args.get("key", "")).strip()
                result = self.user_memory.forget_fact(cat, key)
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "User Memory",
                    "content": f"🧠 [FORGOTTEN] [{cat}] {key}"
                })
                return result

            elif fn_name == "get_user_profile":
                cat = args.get("category")
                facts = self.user_memory.get_facts(category=str(cat).strip() if cat else None)
                return {
                    "status": "success",
                    "category": str(cat).strip() if cat else "all",
                    "count": len(facts),
                    "facts": facts
                }

            elif fn_name == "query_user_memory":
                search_term = str(args.get("search_term", "")).strip()
                category = args.get("category")
                results = self.user_memory.query_facts(
                    search_term=search_term,
                    category=str(category).strip() if category else None,
                    limit=5
                )
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "User Memory",
                    "content": f"🧠 [MEMORY QUERY] '{search_term}' -> {len(results)} matches"
                })
                return {
                    "status": "success",
                    "search_term": search_term,
                    "category": str(category).strip() if category else "all",
                    "count": len(results),
                    "results": results
                }

            elif fn_name == "execute_automation_script":
                script_code = str(args.get("script_code", ""))
                script_type = str(args.get("script_type", "python"))
                description = str(args.get("description", ""))
                result = await asyncio.to_thread(
                    self.script_runner.execute_script,
                    script_code=script_code,
                    script_type=script_type,
                    description=description
                )
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Script Automation",
                    "content": f"⚙️ [{script_type.upper()}] {result.get('message', 'Script executed.')}"
                })
                return result

            else:
                return {
                    "status": "error",
                    "message": f"Unknown tool function '{fn_name}'."
                }

        except Exception as e:
            err_msg = f"Tool execution failed for '{fn_name}': {str(e)}"
            print(f"[DISPATCHER ERROR] {err_msg}")
            self.notify("chat_event", {
                "type": "error",
                "content": err_msg
            })
            return {
                "status": "error",
                "error": str(e),
                "message": err_msg
            }

