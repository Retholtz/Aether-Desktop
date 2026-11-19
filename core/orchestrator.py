"""
App-Agnostic Task Orchestrator for Aether Tier 2 (Gemini 4.0 Hydrogen).

Provides a closed-loop Plan -> Act -> Visually Verify execution engine that allows
Hydrogen to plan dynamic step-by-step checklists for arbitrary desktop/browser tasks,
drive OS/UI primitives across any app (Google Docs, Word, Notepad, Gmail, etc.),
sub-contract deep writing or code generation to Tier 3 (Gemini 4.0 Argon), and
verify on-screen state via multimodal screenshots before reporting completion.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from google.genai import types

logger = logging.getLogger("Aether.Orchestrator")

UPDATE_TASK_CHECKLIST_DECLARATION: Dict[str, Any] = {
    "name": "update_task_checklist",
    "description": (
        "Creates or updates the live step-by-step execution checklist for the current multi-step task. "
        "Call this first with your planned steps (pending), and call it as you verify and check off steps "
        "(in_progress, completed, blocked)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "steps": {
                "type": "ARRAY",
                "description": "Ordered list of checklist steps for completing the task.",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "title": {"type": "STRING", "description": "Concise step description."},
                        "status": {
                            "type": "STRING",
                            "enum": ["pending", "in_progress", "completed", "blocked"],
                            "description": "Current execution state of this step.",
                        },
                        "notes": {"type": "STRING", "description": "Optional verification note or blocker."},
                    },
                    "required": ["title", "status"],
                },
            },
            "current_summary": {
                "type": "STRING",
                "description": "One-line status update describing what step is starting or finished.",
            },
        },
        "required": ["steps"],
    },
}

WAIT_SECONDS_DECLARATION: Dict[str, Any] = {
    "name": "wait_seconds",
    "description": (
        "Pauses briefly (0.5 to 8.0 seconds) while an application launches, a web page finishes redirecting, "
        "or an editor UI finishes rendering before taking a verification screenshot."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "seconds": {
                "type": "NUMBER",
                "description": "Number of seconds to wait (clamped between 0.2 and 8.0).",
            },
            "reason": {
                "type": "STRING",
                "description": "Short reason for waiting (e.g. 'Waiting for Word to finish opening').",
            },
        },
        "required": ["seconds"],
    },
}

# App-agnostic tool allowlist exposed to Tier 2 Hydrogen orchestrator
ORCHESTRATOR_ALLOWED_TOOL_NAMES = {
    "launch_application",
    "focus_window",
    "maximize_window",
    "restore_window",
    "close_window",
    "navigate_browser",
    "capture_screen_snapshot",
    "inspect_screen_context",
    "find_and_click_element",
    "mouse_click",
    "type_text",
    "press_key",
    "scroll_page",
    "paste_to_document",
    "delegate_heavy_task",
    "export_research_dossier",
    "execute_automation_script",
    "search_past_sessions",
    "search_contacts",
    "lookup_contact_email",
    "create_email_draft",
    "list_inbox_messages",
    "search_emails",
    "read_email_thread",
    "list_upcoming_calendar_events",
    "query_user_memory",
    "get_user_profile",
}

ORCHESTRATOR_SYSTEM_INSTRUCTION = """You are Aether's Tier 2 App-Agnostic Task Orchestrator (Gemini 4.0 Hydrogen).
Your job is to take ANY user goal across desktop apps, browsers, files, or emails, design a concrete step-by-step plan, execute each step with tools, and VISUALLY VERIFY the result on screen before concluding.

OPERATING RULES:
1. PLAN FIRST (`update_task_checklist`):
   - On your very first tool turn, call `update_task_checklist` with clear, app-agnostic steps tailored to the specific target app(s) in the user's request (e.g., Google Docs, Microsoft Word, Notepad, Gmail in Chrome, local files, etc.).
   - Keep steps realistic and ordered so dependent resources load cleanly (e.g. opening an app/page, preparing content, verifying focus, inserting content, verifying visually).
2. SUB-CONTRACT HEAVY AUTHORING TO TIER 3 NEON (`delegate_heavy_task`):
   - For exhaustive guides, research dossiers, multi-page reports, complex Python scripts, or heavily calibrated formal writing, call `delegate_heavy_task(..., paste_to_active_doc=False)` so Tier 3 Neon writes and saves the full `.md` file cleanly to disk (`file_path`).
   - Then pass Neon's returned `file_path` directly into `paste_to_document(file_path=..., target_app=...)` AFTER ensuring the target editor is open, focused, and ready. Never paste blindly before checking the window!
   - For short letters, emails, invitations, or notes, you may draft the clean text/Markdown directly.
3. VERIFY STATE & SCREEN VISUALLY (`capture_screen_snapshot`):
   - Do NOT assume `launch_application`, `navigate_browser`, or `paste_to_document` succeeded just because it launched a process.
   - Use `capture_screen_snapshot` after opening an app/URL to confirm it is actually in the foreground and ready for input (for example: handling a Word "Blank document" splash screen, checking whether Google Docs is loaded vs. on a sign-in prompt, or confirming Gmail Compose is visible).
   - After pasting or typing content (`paste_to_document` or `type_text`), confirm via `capture_screen_snapshot` that the expected text is visibly present on screen before marking the final step `completed`.
4. EMAIL, WEBMAIL & APP VIEWING / COMPOSE SAFETY:
   - When asked to open, show, view, or find an email visually in Gmail, Outlook, Chrome, or Edge (e.g., `"Open Gmail and find my wife's chocolate chip cookie recipe"`, `"Open Outlook and show me the email from Pam"`):
     * Launch/navigate to the requested app or browser (`navigate_browser("https://mail.google.com", app_name="chrome")` or `"edge"`, `navigate_browser("https://outlook.live.com", app_name=...)`, or `launch_application("outlook")` if the Outlook desktop client was requested).
     * Call `capture_screen_snapshot` to verify the email client/inbox loaded and is not blocked on a sign-in prompt.
     * Focus the email search bar (`find_and_click_element("search mail box")` or `press_key("/")` in Gmail / `press_key("ctrl+e")` in Outlook), type the requested search keywords with `type_text(..., press_enter=True)`, wait briefly if needed, and capture a snapshot.
     * If a clear matching email appears in the search results, click it to open it on screen and verify via `capture_screen_snapshot` that the email is visibly displayed to the user.
   - When asked to open Gmail / email and write someone a message (e.g., `"Open Gmail and write Stan Orlop a letter..."`):
     * First check `search_contacts` / `lookup_contact_email` for the recipient's email address.
     * If the user specifically asked to open Gmail visibly in the browser, open Gmail compose (`https://mail.google.com/mail/?view=cm&fs=1&to=<email>&su=<subject>` — URL-encoded if recipient/subject are known, or `https://mail.google.com` + click Compose), fill To/Subject/Body, and visually confirm the composed draft is open on screen.
     * NEVER click "Send" automatically unless the user explicitly instructed immediate sending in this request; leave the draft ready on screen for the user to review and confirm.
5. HANDLE FAILURES HONESTLY:
   - If a window or web page fails to focus, inspect a screenshot, adjust (focus/click/navigate), and retry cleanly once or twice.
   - If an app is blocked by the security whitelist or requires user login/action, mark the step `blocked`, stop cleanly, and explain the exact step and blocker.
   - Never claim a task succeeded on screen unless you verified it.
"""


def _extract_candidate_text(response: Any) -> str:
    """Extracts non-thought assistant text from a GenAI generate_content response."""
    text_parts: List[str] = []
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            t = getattr(part, "text", None)
            if t and not getattr(part, "thought", False):
                text_parts.append(str(t))
    if not text_parts:
        fallback = getattr(response, "text", None)
        if fallback:
            text_parts.append(str(fallback))
    return "".join(text_parts).strip()


def _prune_stale_screenshots(contents: List[Any]) -> None:
    """Replaces older inline image bytes in conversation history to keep loop payloads small."""
    img_refs: List[tuple] = []
    for content_obj in contents:
        parts = getattr(content_obj, "parts", None)
        if not parts:
            continue
        for idx, part in enumerate(parts):
            inl = getattr(part, "inline_data", None)
            if inl and str(getattr(inl, "mime_type", "")).startswith("image/"):
                img_refs.append((parts, idx))
    if len(img_refs) > 1:
        for parts_list, p_idx in img_refs[:-1]:
            parts_list[p_idx] = types.Part.from_text(
                text="[Prior desktop verification screenshot omitted after inspection]"
            )


class TaskOrchestrator:
    """
    Runs a bounded multi-turn Plan -> Act -> Visually Verify loop on Gemini 4.0 Hydrogen.
    """

    def __init__(
        self,
        dispatcher: Any,
        max_iterations: int = 15,
    ):
        self.dispatcher = dispatcher
        self.max_iterations = max(3, int(max_iterations))
        self.checklist: List[Dict[str, Any]] = []
        self.tools_used: List[str] = []

    def _collect_tool_declarations(self) -> List[Dict[str, Any]]:
        """Builds the app-agnostic tool declarations exposed to Hydrogen."""
        from tools.dispatcher import get_all_tool_declarations

        selected: List[Dict[str, Any]] = [
            UPDATE_TASK_CHECKLIST_DECLARATION,
            WAIT_SECONDS_DECLARATION,
        ]
        seen = {UPDATE_TASK_CHECKLIST_DECLARATION["name"], WAIT_SECONDS_DECLARATION["name"]}
        for decl in get_all_tool_declarations():
            name = decl.get("name")
            if name in ORCHESTRATOR_ALLOWED_TOOL_NAMES and name not in seen:
                selected.append(decl)
                seen.add(name)
        return selected

    def _handle_checklist_update(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Updates internal checklist state and emits formatted UI progress events."""
        raw_steps = args.get("steps") or []
        normalized: List[Dict[str, Any]] = []
        for idx, item in enumerate(raw_steps, start=1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or f"Step {idx}").strip()
            status = str(item.get("status") or "pending").strip().lower()
            if status not in ("pending", "in_progress", "completed", "blocked"):
                status = "pending"
            notes = str(item.get("notes") or "").strip()
            normalized.append({"step": idx, "title": title, "status": status, "notes": notes})

        if normalized:
            self.checklist = normalized

        done_cnt = sum(1 for s in self.checklist if s["status"] == "completed")
        total_cnt = len(self.checklist)
        summary = str(args.get("current_summary") or "").strip()

        icons = {"completed": "[x]", "in_progress": "[>]", "blocked": "[!]", "pending": "[ ]"}
        lines = [f"{icons.get(s['status'], '[ ]')} {s['step']}. {s['title']}" + (f" ({s['notes']})" if s["notes"] else "") for s in self.checklist]
        checklist_text = "\n".join(lines)

        action_label = summary or f"Plan progress ({done_cnt}/{total_cnt})"
        self.dispatcher.notify("status", {
            "state": "executing",
            "action": f"📋 {action_label}",
            "message": f"Hydrogen Orchestrator ({done_cnt}/{total_cnt}): {action_label}",
        })
        self.dispatcher.notify("chat_event", {
            "type": "tool",
            "name": "Hydrogen Task Plan",
            "content": f"📋 [PLAN {done_cnt}/{total_cnt}] {summary}\n{checklist_text}".strip(),
        })
        return {
            "status": "success",
            "completed_steps": done_cnt,
            "total_steps": total_cnt,
            "checklist": self.checklist,
        }

    async def _execute_tool_call(
        self,
        fn_name: str,
        fn_args: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Optional[bytes], str]:
        """Executes a single tool call and returns (sanitized_result, optional_jpeg_bytes, mime_type)."""
        if fn_name not in self.tools_used:
            self.tools_used.append(fn_name)

        if fn_name == "update_task_checklist":
            return self._handle_checklist_update(fn_args), None, "image/jpeg"

        if fn_name == "wait_seconds":
            try:
                sec = float(fn_args.get("seconds", 1.5))
            except (TypeError, ValueError):
                sec = 1.5
            sec = max(0.2, min(8.0, sec))
            reason = str(fn_args.get("reason") or "Waiting for UI readiness").strip()
            await asyncio.sleep(sec)
            return {"status": "success", "waited_seconds": sec, "reason": reason}, None, "image/jpeg"

        self.dispatcher.notify("status", {
            "state": "executing",
            "action": f"⚙️ {fn_name.replace('_', ' ').title()}...",
            "message": f"Hydrogen executing {fn_name}...",
        })
        raw_res = await self.dispatcher.dispatch(fn_name, fn_args)
        result = dict(raw_res) if isinstance(raw_res, dict) else {"result": raw_res}

        jpeg_bytes: Optional[bytes] = None
        mime_type = "image/jpeg"
        if fn_name == "inspect_screen_context" and "image_bytes" in result:
            jpeg_bytes = result.pop("image_bytes")
            mime_type = str(result.get("mime_type") or "image/jpeg")
        elif "_jpeg_bytes" in result:
            jpeg_bytes = result.pop("_jpeg_bytes")

        return result, jpeg_bytes, mime_type

    async def run(
        self,
        client: Any,
        model: str,
        thinking_level: str,
        title: str,
        instructions: str,
        context_data: str = "",
        use_google_search: bool = False,
    ) -> Dict[str, Any]:
        """Executes an app-agnostic multi-step orchestration loop with Hydrogen."""
        from core.model_discovery import build_thinking_config

        loop = asyncio.get_running_loop()
        tool_decls = self._collect_tool_declarations()
        tools_list: List[Any] = [types.Tool(function_declarations=tool_decls)]
        if use_google_search:
            tools_list.insert(0, types.Tool(google_search=types.GoogleSearch()))

        cfg_kwargs: Dict[str, Any] = {
            "tools": tools_list,
            "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
            "system_instruction": ORCHESTRATOR_SYSTEM_INSTRUCTION,
            "thinking_config": build_thinking_config(model, thinking_level=thinking_level),
            "temperature": 0.2,
        }
        if use_google_search:
            cfg_kwargs["tool_config"] = types.ToolConfig(include_server_side_tool_invocations=True)

        gen_config = types.GenerateContentConfig(**cfg_kwargs)

        user_msg = f"Task Title: {title}\nGoal / Instructions:\n{instructions.strip()}"
        if context_data and str(context_data).strip():
            user_msg += f"\n\nAdditional Context:\n{str(context_data).strip()}"

        contents: List[Any] = [
            types.Content(role="user", parts=[types.Part.from_text(text=user_msg)])
        ]

        last_fail_sig: Optional[str] = None
        repeat_fail_count = 0
        blocked_app_msg: Optional[str] = None
        final_answer = ""

        for iteration in range(1, self.max_iterations + 1):
            response = await loop.run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=gen_config,
                ),
            )

            candidates = getattr(response, "candidates", None) or []
            if candidates and getattr(candidates[0], "content", None):
                contents.append(candidates[0].content)

            fn_calls = getattr(response, "function_calls", None) or []
            if not fn_calls:
                final_answer = _extract_candidate_text(response)
                break

            tool_parts: List[Any] = []
            for fc in fn_calls:
                fn_name = getattr(fc, "name", "")
                raw_args = getattr(fc, "args", None)
                fn_args = dict(raw_args) if isinstance(raw_args, dict) else (dict(raw_args) if raw_args else {})
                res_dict, jpeg_bytes, mime_type = await self._execute_tool_call(fn_name, fn_args)

                if res_dict.get("status") == "blocked":
                    blocked_app_msg = str(
                        res_dict.get("message") or res_dict.get("error") or "Target application is blocked by whitelist."
                    )

                if res_dict.get("status") in ("error", "not_found", "blocked"):
                    sig = f"{fn_name}:{json.dumps(fn_args, sort_keys=True, default=str)}"
                    if sig == last_fail_sig:
                        repeat_fail_count += 1
                    else:
                        last_fail_sig = sig
                        repeat_fail_count = 1
                else:
                    last_fail_sig = None
                    repeat_fail_count = 0

                tool_parts.append(
                    types.Part.from_function_response(name=fn_name, response=res_dict)
                )
                if jpeg_bytes:
                    tool_parts.append(types.Part.from_bytes(data=jpeg_bytes, mime_type=mime_type))

            contents.append(types.Content(role="user", parts=tool_parts))
            _prune_stale_screenshots(contents)

            if blocked_app_msg:
                logger.warning(f"[ORCHESTRATOR] Halting workflow due to whitelist block: {blocked_app_msg}")
                final_answer = blocked_app_msg
                break

            if repeat_fail_count >= 3:
                logger.warning(f"[ORCHESTRATOR] Halting workflow after repeated identical failure on {last_fail_sig}")
                final_answer = (
                    f"Workflow paused after 3 failed attempts on step '{last_fail_sig}'. "
                    "Check the active window/sign-in state and retry."
                )
                break
        else:
            final_answer = (
                "Reached maximum orchestration steps without final text confirmation. "
                "Please check the screen state."
            )

        if not final_answer:
            final_answer = "Workflow completed."

        completed_steps = sum(1 for s in self.checklist if s.get("status") == "completed")
        status = "blocked" if blocked_app_msg else ("error" if repeat_fail_count >= 3 else "success")
        return {
            "status": status,
            "model": model,
            "thinking_level": thinking_level,
            "answer": final_answer,
            "checklist": self.checklist,
            "steps_completed": completed_steps,
            "total_steps": len(self.checklist),
            "tools_used": self.tools_used,
        }
