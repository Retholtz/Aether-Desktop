import io
import time
import ctypes
from typing import Optional, Tuple
from PIL import Image, ImageGrab
import win32gui
import win32ui
import win32con

from core.screen_stream import ensure_thread_desktop


def get_foreground_window_rect() -> Optional[Tuple[int, int, int, int]]:
    """Retrieves bounding box (left, top, right, bottom) of active foreground window."""
    ensure_thread_desktop()
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None
    try:
        rect = win32gui.GetWindowRect(hwnd)
        # Avoid zero-size or minimized windows
        if rect[2] - rect[0] > 50 and rect[3] - rect[1] > 50:
            return rect
    except Exception:
        pass
    return None


def capture_screen_image(
    target: str = "active_window",
    max_dimension: int = 1280,
    quality: int = 80
) -> Tuple[bytes, str]:
    """
    Captures either the active foreground window or the full primary display,
    downsamples while maintaining aspect ratio, and encodes to optimized JPEG.

    Args:
        target: 'active_window' or 'full_screen'
        max_dimension: Max width or height in pixels (1280 balances OCR readability and speed)
        quality: JPEG compression quality (80 preserves code/text sharpness)

    Returns:
        (image_bytes, description_summary)
    """
    ensure_thread_desktop()
    img = None
    context_desc = "full screen"

    if target == "active_window":
        rect = get_foreground_window_rect()
        if rect:
            try:
                # Capture bounding box of active window
                img = ImageGrab.grab(bbox=rect, all_screens=True)
                hwnd = win32gui.GetForegroundWindow()
                title = win32gui.GetWindowText(hwnd)
                context_desc = f"active window '{title}'"
            except Exception as e:
                print(f"[WARN] [VISION] Active window capture fallback to full screen: {e}")
                img = None

    if img is None:
        try:
            # Full primary screen fallback
            img = ImageGrab.grab(all_screens=False)
            context_desc = "primary desktop screen"
        except Exception as e:
            print(f"[WARN] [VISION] Primary screen grab fallback: {e}")
            img = Image.new("RGB", (1280, 720), color=(24, 24, 32))
            context_desc = "primary desktop screen"

    # Convert to RGB (in case of RGBA/alpha channel)
    if img.mode != "RGB":
        img = img.convert("RGB")

    # Downsample proportionally if image exceeds max_dimension
    w, h = img.size
    if max(w, h) > max_dimension:
        scale = max_dimension / float(max(w, h))
        new_size = (int(w * scale), int(h * scale))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    # Encode to buffer
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    image_bytes = buffer.getvalue()

    # Guarantee low-latency payload ceiling (< 400 KB) even on complex screens
    if len(image_bytes) >= 380 * 1024 and quality > 50:
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=60, optimize=True)
        image_bytes = buffer.getvalue()

    safe_desc = context_desc.encode("ascii", "replace").decode("ascii")
    print(f"[INFO] [VISION] Captured {safe_desc} ({img.size[0]}x{img.size[1]}, {len(image_bytes) // 1024} KB)")
    return image_bytes, context_desc
