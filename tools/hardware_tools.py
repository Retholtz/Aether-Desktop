"""
tools/hardware_tools.py - Agent Tools for System Thermals, VRAM, and FPS
Exposes queries for system performance during gaming and heavy compute.
"""
from typing import Dict, Any
from core.hardware_service import HardwareService


def register_hardware_tools(dispatcher, service: HardwareService):
    """Registers hardware telemetry tools on Aether's ToolDispatcher."""

    async def get_hardware_telemetry() -> Dict[str, Any]:
        """
        Retrieves real-time GPU temperature, GPU utilization, VRAM usage,
        system CPU/RAM, and live FPS/frametime metrics (if active game is monitored).
        """
        import asyncio
        snap = service.get_snapshot()
        if snap.fps_avg is None and snap.active_game_process:
            # Wait briefly if PresentMon just started and hasn't flushed its first CSV rows yet
            for _ in range(6):
                await asyncio.sleep(0.25)
                snap = service.get_snapshot()
                if snap.fps_avg is not None:
                    break
        data = snap.to_dict()

        # Build natural language summary for voice synthesis
        summary_parts = []
        cpu_label = f"CPU ({snap.cpu_name})" if snap.cpu_name else "CPU"
        if snap.cpu_temp_c is not None:
            summary_parts.append(f"{cpu_label}: {snap.cpu_temp_c}°C at {snap.cpu_load_pct:.0f}% load")
        else:
            summary_parts.append(f"{cpu_label}: {snap.cpu_load_pct:.0f}% load")

        gpu_label = f"GPU ({snap.gpu_name})" if snap.gpu_name else "GPU"
        summary_parts.append(f"{gpu_label}: {snap.gpu_temp_c}°C at {snap.gpu_utilization_pct}% load")
        summary_parts.append(f"VRAM: {snap.vram_used_gb:.1f} of {snap.vram_total_gb:.1f} GB ({snap.vram_pct:.0f}%)")

        if snap.fps_avg is not None:
            summary_parts.append(f"Framerate: {snap.fps_avg} FPS (1% Low: {snap.fps_1pct_low} FPS)")
        elif snap.active_game_process:
            summary_parts.append(f"Framerate: Collecting frames for {snap.active_game_process}")
        else:
            summary_parts.append("Framerate: Inactive (no monitored game running or FPS tracking disabled)")

        return {
            "status": "success",
            "telemetry": data,
            "spoken_summary": ", ".join(summary_parts)
        }

    decl = {
        "name": "get_hardware_telemetry",
        "description": "Returns GPU thermals, VRAM usage, CPU load, and real-time game FPS/1% lows.",
        "parameters": {"type": "object", "properties": {}}
    }

    try:
        dispatcher.register_tool(
            name="get_hardware_telemetry",
            description="Returns GPU thermals, VRAM usage, CPU load, and real-time game FPS/1% lows.",
            parameters={"type": "object", "properties": {}},
            handler=get_hardware_telemetry
        )
    except TypeError:
        dispatcher.register_tool("get_hardware_telemetry", decl, get_hardware_telemetry)

