"""
Aether Desktop - Screen Capture & Multi-Monitor Vision Stream
Captures desktop screens across single and multi-monitor setups using MSS,
resizes/optimizes frames for Gemini Multimodal Live, and supports active-window tracking.
"""

import asyncio
import io
import sys
import time
from typing import Dict, List, Optional, Tuple

import mss
from PIL import Image

if sys.platform == "win32":
    import win32api
    import win32con
    import win32gui
    import win32service
else:
    win32gui = None


_GLOBAL_DESKTOP_HANDLE = None

def ensure_thread_desktop():
    """Attaches current thread to user's interactive Windows desktop."""
    global _GLOBAL_DESKTOP_HANDLE
    if sys.platform == "win32" and win32service:
        try:
            if _GLOBAL_DESKTOP_HANDLE is None:
                _GLOBAL_DESKTOP_HANDLE = win32service.OpenDesktop("default", 0, False, win32con.GENERIC_ALL)
            _GLOBAL_DESKTOP_HANDLE.SetThreadDesktop()
        except Exception:
            try:
                hdesk = win32service.OpenDesktop("default", 0, False, win32con.GENERIC_ALL)
                hdesk.SetThreadDesktop()
                _GLOBAL_DESKTOP_HANDLE = hdesk
            except Exception:
                pass

# Ensure desktop attachment immediately on module import
ensure_thread_desktop()


class ScreenCapturePipeline:
    """Manages high-performance multi-monitor screen capture and JPEG compression."""

    def __init__(self):
        ensure_thread_desktop()
        self._sct: Optional[mss.MSS] = None
        self.monitors: List[Dict] = []
        self.last_streamed_monitor_index: int = 1
        self.refresh_monitors()

    def refresh_monitors(self) -> List[Dict]:
        """Queries and updates connected display metadata."""
        ensure_thread_desktop()
        try:
            with mss.MSS() as sct:
                self.monitors = list(sct.monitors)
        except Exception as e:
            print(f"[VISION ERROR] Failed to enumerate monitors: {e}")
            self.monitors = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]
        return self.monitors

    def get_monitor_list_for_ui(self) -> List[Dict]:
        """Returns friendly descriptions of connected monitors for the GUI settings."""
        self.refresh_monitors()
        result = [
            {
                "id": "auto",
                "name": "Auto (Follow Active / Focused Window)",
                "details": "Streams whichever display has your active window"
            },
            {
                "id": "all",
                "name": "All Displays Combined (Virtual Desktop)",
                "details": f"{self.monitors[0]['width']}x{self.monitors[0]['height']}" if self.monitors else "All"
            }
        ]
        for idx in range(1, len(self.monitors)):
            m = self.monitors[idx]
            is_pri = m.get("is_primary", False) or idx == 1
            tag = " [Primary]" if is_pri else ""
            result.append({
                "id": str(idx),
                "name": f"Monitor {idx}: {m['width']}x{m['height']}{tag}",
                "details": f"Position ({m['left']}, {m['top']})"
            })
        return result

    def get_active_monitor_index(self) -> int:
        """Identifies which monitor contains the currently active / foreground window."""
        if not win32gui:
            return 1
        ensure_thread_desktop()
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return 1
            hmon = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
            info = win32api.GetMonitorInfo(hmon)
            rc = info.get("rcMonitor", (0, 0, 0, 0))  # (left, top, right, bottom)
            for idx in range(1, len(self.monitors)):
                m = self.monitors[idx]
                if m["left"] == rc[0] and m["top"] == rc[1]:
                    return idx
            return 1
        except Exception:
            return 1

    def resolve_target_monitor(self, target: str | int = "auto") -> Tuple[int, Dict]:
        """Resolves target string/index to a specific monitor dictionary."""
        self.refresh_monitors()
        if not self.monitors:
            return 0, {"left": 0, "top": 0, "width": 1920, "height": 1080}

        target_str = str(target).lower().strip()
        if target_str == "auto":
            idx = self.get_active_monitor_index()
            if idx == 0 and getattr(self, "last_streamed_monitor_index", 1) > 0:
                idx = self.last_streamed_monitor_index
            return idx, self.monitors[idx] if idx < len(self.monitors) else self.monitors[0]
        elif target_str in ("all", "0", "virtual", "combined"):
            return 0, self.monitors[0]
        elif target_str in ("primary", "1"):
            idx = 1 if len(self.monitors) > 1 else 0
            return idx, self.monitors[idx]
        elif target_str in ("secondary", "2"):
            idx = 2 if len(self.monitors) > 2 else (1 if len(self.monitors) > 1 else 0)
            return idx, self.monitors[idx]
        else:
            try:
                num = int(target_str)
                if 0 <= num < len(self.monitors):
                    return num, self.monitors[num]
            except ValueError:
                pass
            return 1 if len(self.monitors) > 1 else 0, self.monitors[1] if len(self.monitors) > 1 else self.monitors[0]

    def capture_frame_sync(
        self,
        target: str | int = "auto",
        max_dim: int = 1024,
        quality: int = 75
    ) -> Tuple[bytes, Dict]:
        """
        Synchronously grabs a screenshot of the specified monitor,
        downscales to max_dim, and returns (jpeg_bytes, metadata).
        """
        ensure_thread_desktop()
        mon_idx, mon_rect = self.resolve_target_monitor(target)
        self.last_streamed_monitor_index = mon_idx

        with mss.MSS() as sct:
            sct_img = sct.grab(mon_rect)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

            orig_w, orig_h = img.size
            img.thumbnail((max_dim, max_dim), Image.Resampling.BILINEAR)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            jpeg_bytes = buf.getvalue()

            metadata = {
                "monitor_index": mon_idx,
                "monitor_rect": mon_rect,
                "original_size": (orig_w, orig_h),
                "captured_size": img.size,
                "jpeg_size_bytes": len(jpeg_bytes),
                "timestamp": time.time()
            }
            return jpeg_bytes, metadata

    async def capture_frame(
        self,
        target: str | int = "auto",
        max_dim: int = 1024,
        quality: int = 75
    ) -> Tuple[bytes, Dict]:
        """Asynchronously captures a frame using the asyncio executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self.capture_frame_sync,
            target,
            max_dim,
            quality
        )
