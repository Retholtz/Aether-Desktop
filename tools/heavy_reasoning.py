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
    from tools.dispatcher import ToolDispatcher
    dispatcher = ToolDispatcher()

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(
                    asyncio.run,
                    dispatcher.execute_heavy_task(
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
                )
                return fut.result()
        else:
            return loop.run_until_complete(
                dispatcher.execute_heavy_task(
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
            )
    except Exception as e:
        return {"status": "error", "message": f"Heavy reasoning execution failed: {e}"}

