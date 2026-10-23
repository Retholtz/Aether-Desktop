"""
Aether Heavy Reasoning Utility: tools/heavy_reasoning.py
Provides direct and script-level access to Tier 2 Heavy Reasoning (Gemini 4 Argon).
"""

import asyncio
from typing import Optional, Dict, Any


def delegate_heavy_task(
    task_type: str = "research_dossier",
    title: str = "Task",
    instructions: str = "",
    context_data: str = "",
    category: str = "general",
    open_immediately: bool = False,
    execute_if_code: bool = True,
    paste_to_target: bool = False,
    target_app: str = "chrome"
) -> Dict[str, Any]:
    """Synchronous entrypoint for automation scripts delegating heavy reasoning to Gemini 4 Argon."""
    import json
    import os
    from tools.dispatcher import ToolDispatcher

    def _load_default_config() -> dict:
        try:
            from core.config_manager import load_config
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cfg_path = os.path.join(base_dir, "config.json")
            return load_config(cfg_path)
        except Exception:
            try:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                cfg_path = os.path.join(base_dir, "config.json")
                if os.path.exists(cfg_path):
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception:
                pass
        return {}

    dispatcher = ToolDispatcher(config_getter=_load_default_config)

    async def _invoke():
        return await dispatcher.execute_heavy_task(
            task_type=task_type,
            title=title,
            instructions=instructions,
            context_data=context_data,
            category=category,
            open_immediately=open_immediately,
            execute_if_code=execute_if_code,
            paste_to_active_doc=paste_to_target,
            target_app=target_app
        )

    try:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop and running_loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(asyncio.run, _invoke())
                return fut.result()
        else:
            return asyncio.run(_invoke())
    except Exception as e:
        return {"status": "error", "message": f"Heavy reasoning execution failed: {e}"}

