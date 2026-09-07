"""
Aether Desktop - Central Tool Router & Execution Dispatcher
Maps Gemini Multimodal Live tool calls directly to their respective execution tiers,
enforcing complete exception isolation so tool faults cannot block or crash the WebSocket/audio pipeline.
"""

import asyncio
from typing import Callable, Dict, List, Optional

from core.screen_stream import ScreenCapturePipeline
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
        "Adds an application (e.g. calculator, calc.exe, steam, discord, paint) to the user's security whitelist "
        "so that it can be launched. Call this when the user asks to add, permit, or allow a program on the whitelist, "
        "or when an application was blocked and the user instructs to add it or allow it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "The name or executable of the application to add to the whitelist (e.g. 'calculator', 'calc.exe', 'steam', 'discord')."
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

MOUSE_CLICK_DECLARATION = {
    "name": "mouse_click",
    "description": (
        "Clicks on a button, icon, link, or coordinate on the desktop screen. "
        "Use normalized coordinates from 0 to 1000 based on what you see in the vision stream "
        "(0,0 is top-left, 1000,1000 is bottom-right). If targeting an element inside an application window "
        "(such as Chrome or Notepad), pass app_name to map coordinates accurately inside that window."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "x": {
                "type": "NUMBER",
                "description": "The X coordinate (0-1000 normalized across the screen or window width)."
            },
            "y": {
                "type": "NUMBER",
                "description": "The Y coordinate (0-1000 normalized across the screen or window height)."
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
                "description": "Optional name of the target application window (e.g. 'chrome', 'notepad'). When provided, (x, y) coordinates are mapped directly within the window's physical bounds, guaranteeing clicks stay inside the window."
            }
        },
        "required": ["x", "y"]
    }
}

TYPE_TEXT_DECLARATION = {
    "name": "type_text",
    "description": (
        "Types text into the active focused field or specified application window. "
        "Supports all Unicode characters and symbols. Set press_enter=true to submit the text or search query."
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
                "description": "Optional application to bring to focus before typing (e.g. 'notepad', 'word', 'chrome', 'excel')."
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

EXECUTE_AUTOMATION_SCRIPT_DECLARATION = {
    "name": "execute_automation_script",
    "description": (
        "Generates and executes an on-the-fly Python or PowerShell automation script in the background. "
        "Use this for complex operations such as creating or formatting tables in Word or Excel, "
        "populating spreadsheets, calculating formulas, editing documents, file batching, or system tasks. "
        "Python scripts run with full access to win32com.client (e.g. win32com.client.Dispatch('Excel.Application') "
        "or Dispatch('Word.Application')) for native real-time Microsoft Office automation."
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
                "description": "A clear 1-line description of what this automation script does (e.g. 'Format sales table in Word', 'Populate quarterly revenue in Excel')."
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
        MOUSE_CLICK_DECLARATION,
        TYPE_TEXT_DECLARATION,
        PRESS_KEY_DECLARATION,
        SCROLL_PAGE_DECLARATION,
        CAPTURE_SCREEN_SNAPSHOT_DECLARATION,
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
    ):
        self.screen_pipeline = screen_pipeline or ScreenCapturePipeline()
        self.gui_controller = GuiPrimitivesController(self.screen_pipeline)
        self.script_runner = ScriptRunner()
        self.on_event = on_event
        self.whitelist_getter = whitelist_getter or (lambda: [])
        self.whitelist_updater = whitelist_updater
        self.last_blocked_app = ""

    def notify(self, event_type: str, data: dict):
        if self.on_event:
            try:
                self.on_event(event_type, data)
            except Exception as e:
                print(f"[DISPATCHER NOTIFY ERROR] {e}")

    async def dispatch(self, fn_name: str, fn_args: dict) -> dict:
        """
        Executes the named function with the given arguments.
        Enforces complete exception isolation so no tool failure can crash the main WebSocket session.
        """
        args = fn_args or {}

        try:
            # -------------------------------------------------------------
            # Tier 1: Win32 Native Window Management
            # -------------------------------------------------------------
            if fn_name == "maximize_window":
                app_name = str(args.get("app_name", "")).strip()
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
                result = restore_window(app_name)
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Window Control",
                    "content": f"🪟 [RESTORE] {result.get('message', app_name)}"
                })
                return result

            elif fn_name == "focus_window":
                app_name = str(args.get("app_name") or args.get("window_name") or "").strip()
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
                if (not app_name or app_name.lower() in ("it", "that", "this", "the app", "the program")) and self.last_blocked_app:
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
                whitelist = self.whitelist_getter()
                result = navigate_browser(query_or_url, app_name=app_name, whitelist=whitelist)
                self.notify("chat_event", {
                    "type": "tool",
                    "name": "Browser Control",
                    "content": f"🌐 [NAVIGATE] {app_name}: {result.get('message', query_or_url)}"
                })
                return result

            # -------------------------------------------------------------
            # Tier 2: Low-Level GUI Primitives
            # -------------------------------------------------------------
            elif fn_name == "mouse_click":
                x = float(args.get("x", 500))
                y = float(args.get("y", 500))
                button = str(args.get("button", "left"))
                clicks = int(args.get("clicks", 1))
                monitor = args.get("monitor", "auto")
                app_name = args.get("app_name", None)
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
                app_name = args.get("app_name", None)
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
                jpeg_bytes, meta = await self.screen_pipeline.capture_frame(target=monitor, max_dim=1280, quality=85)
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
            # Tier 3: Sandboxed Script Runner
            # -------------------------------------------------------------
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

