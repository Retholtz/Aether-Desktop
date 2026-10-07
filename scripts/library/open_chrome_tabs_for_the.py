import os
import shutil
from typing import Sequence
import win32api
import win32event
import win32process

DEFAULT_URLS = (
    "https://www.realtor.com/realestateandhomes-detail/16309-Kellogg-Rd_Bowling-Green_OH_43402",
    "https://www.realtor.com/realestateandhomes-detail/28920-Bates-Rd_Perrysburg_OH_43551",
    "https://www.zillow.com/homedetails/470-Edgewood-Dr-Perrysburg-OH-43551/35767220_zpid/",
)


def get_chrome_executable() -> str:
    """Resolve the Chrome binary path deterministically without registry access."""
    candidates = [
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return "chrome.exe"


def open_listing_tabs(urls: Sequence[str] = DEFAULT_URLS, timeout_ms: int = 5000) -> None:
    """
    Launch Chrome with all target URLs simultaneously in a single process invocation.
    Replaces non-deterministic sleep polling with Win32 WaitForInputIdle and ensures
    all process/thread handles are released in a finally block.
    """
    if not urls:
        return

    chrome_path = get_chrome_executable()
    formatted_urls = " ".join(f'"{url}"' for url in urls)
    command_line = f'"{chrome_path}" {formatted_urls}'

    startup_info = win32process.STARTUPINFO()
    h_process, h_thread = None, None

    try:
        h_process, h_thread, _, _ = win32process.CreateProcess(
            None,
            command_line,
            None,
            None,
            False,
            0,
            None,
            None,
            startup_info,
        )
        # Deterministic wait for the browser process to initialize and enter idle state
        win32event.WaitForInputIdle(h_process, timeout_ms)
        print("Opened listing tabs successfully.")
    finally:
        if h_thread is not None:
            win32api.CloseHandle(h_thread)
        if h_process is not None:
            win32api.CloseHandle(h_process)


if __name__ == "__main__":
    open_listing_tabs()