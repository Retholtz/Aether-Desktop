import time
import win32clipboard
import win32con
import win32gui

from core.screen_stream import ensure_thread_desktop
from tools.gui_primitives import GuiPrimitivesController
from tools.os_controls import bring_hwnd_to_foreground, find_hwnd_by_query

# Parameterized Target & Payload Configuration
TARGET_QUERIES = ("Google Docs", "Chrome")
CLICK_TARGET = {"x": 500, "y": 320, "app_name": "chrome"}

HTML_RECIPE = """
<h1>Ina Garten's Perfect Roast Chicken</h1>
<p><em>The Barefoot Contessa's most famous, foolproof roast chicken with tender, caramelized aromatic vegetables.</em></p>
<p><strong>Prep Time:</strong> 20 mins &nbsp;|&nbsp; <strong>Cook Time:</strong> 1 hr 30 mins &nbsp;|&nbsp; <strong>Rest Time:</strong> 20 mins &nbsp;|&nbsp; <strong>Servings:</strong> 4 to 6</p>
<hr/>
<h2>Ingredients</h2>
<ul>
    <li>1 (5 to 6-pound) roasting chicken</li>
    <li>Kosher salt and freshly ground black pepper</li>
    <li>1 large bunch fresh thyme, plus 20 sprigs fresh thyme</li>
    <li>1 lemon, halved</li>
    <li>1 head garlic, cut in half crosswise</li>
    <li>2 tablespoons (1/4 stick) unsalted butter, melted</li>
    <li>1 large yellow onion, thickly sliced</li>
    <li>2 carrots, peeled and cut into 2-inch chunks</li>
    <li>1 bulb fennel, tops removed and cut into wedges</li>
    <li>Olive oil</li>
</ul>
<hr/>
<h2>Instructions</h2>
<ol>
    <li><strong>Preheat Oven:</strong> Preheat oven to 425°F (220°C).</li>
    <li><strong>Prep Chicken:</strong> Remove giblets. Rinse chicken inside and out. Remove excess fat and pat the outside completely dry.</li>
    <li><strong>Season Cavity:</strong> Liberally salt and pepper cavity. Stuff with thyme bunch, lemon halves, and both garlic halves.</li>
    <li><strong>Truss & Brush:</strong> Brush outside with melted butter and sprinkle generously with salt and pepper. Tie legs with kitchen twine and tuck wing tips under.</li>
    <li><strong>Prepare Vegetables:</strong> Toss onion, carrots, fennel, remaining thyme, and 2 tbsp olive oil with salt and pepper in roasting pan. Set chicken on top.</li>
    <li><strong>Roast:</strong> Roast 1 hour 15 minutes to 1 1/2 hours until internal temperature reaches 165°F in thigh.</li>
    <li><strong>Rest & Serve:</strong> Tent with foil for 15-20 minutes before carving. Serve with pan juices and roasted vegetables.</li>
</ol>
"""

PLAIN_RECIPE = """Ina Garten's Perfect Roast Chicken
The Barefoot Contessa's most famous, foolproof roast chicken with tender aromatic vegetables.

Prep Time: 20 mins | Cook Time: 1 hr 30 mins | Servings: 4-6

Ingredients:
- 1 (5 to 6-pound) roasting chicken
- Kosher salt and freshly ground black pepper
- 1 large bunch fresh thyme, plus 20 sprigs fresh thyme
- 1 lemon, halved
- 1 head garlic, cut in half crosswise
- 2 tablespoons unsalted butter, melted
- 1 large yellow onion, thickly sliced
- 2 carrots, peeled and cut into 2-inch chunks
- 1 bulb fennel, tops removed and cut into wedges
- Olive oil

Instructions:
1. Preheat oven to 425°F (220°C).
2. Prep Chicken: Remove giblets, rinse inside/out, pat thoroughly dry.
3. Season Cavity: Season cavity with salt/pepper; stuff with thyme, lemon, garlic.
4. Truss & Brush: Brush with melted butter, season well. Tie legs and tuck wing tips.
5. Prepare Vegetables: Toss onions, carrots, fennel, thyme, olive oil in pan. Place chicken on top.
6. Roast: Roast for 1 hr 15 mins to 1 1/2 hrs (165°F).
7. Rest & Serve: Rest 15-20 mins before carving.
"""


def format_windows_html_clipboard(html_fragment: str) -> bytes:
    """Prepares HTML string with standard Windows CF_HTML offsets."""
    prefix = "<html><body>\r\n<!--StartFragment-->"
    suffix = "<!--EndFragment-->\r\n</body></html>"
    header_template = (
        "Version:0.9\r\n"
        "StartHTML:{start_html:010d}\r\n"
        "EndHTML:{end_html:010d}\r\n"
        "StartFragment:{start_frag:010d}\r\n"
        "EndFragment:{end_frag:010d}\r\n"
    )

    dummy_header = header_template.format(start_html=0, end_html=0, start_frag=0, end_frag=0)
    header_len = len(dummy_header.encode("utf-8"))

    prefix_bytes = prefix.encode("utf-8")
    fragment_bytes = html_fragment.encode("utf-8")
    suffix_bytes = suffix.encode("utf-8")

    start_html = header_len
    start_frag = start_html + len(prefix_bytes)
    end_frag = start_frag + len(fragment_bytes)
    end_html = end_frag + len(suffix_bytes)

    header = header_template.format(
        start_html=start_html,
        end_html=end_html,
        start_frag=start_frag,
        end_frag=end_frag,
    ).encode("utf-8")

    return header + prefix_bytes + fragment_bytes + suffix_bytes


def set_clipboard_payload(html_data: bytes, text_data: str, retries: int = 5) -> None:
    """Safely updates the clipboard with multi-format content and deterministic retry."""
    cf_html = win32clipboard.RegisterClipboardFormat("HTML Format")
    for attempt in range(retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(cf_html, html_data)
                win32clipboard.SetClipboardText(text_data, win32con.CF_UNICODETEXT)
                return
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(0.02)


def wait_for_foreground_window(hwnd: int, timeout: float = 0.5) -> bool:
    """Deterministically waits until the specified HWND is in the foreground."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.01)
    return win32gui.GetForegroundWindow() == hwnd


def main() -> None:
    ensure_thread_desktop()

    # Stage clipboard payload prior to UI interactions
    formatted_html = format_windows_html_clipboard(HTML_RECIPE)
    set_clipboard_payload(formatted_html, PLAIN_RECIPE)

    hwnd = None
    for query in TARGET_QUERIES:
        hwnd = find_hwnd_by_query(query)
        if hwnd:
            break

    if hwnd:
        bring_hwnd_to_foreground(hwnd)
        wait_for_foreground_window(hwnd, timeout=0.5)

    gui = GuiPrimitivesController()
    gui.click(CLICK_TARGET["x"], CLICK_TARGET["y"], app_name=CLICK_TARGET["app_name"])
    gui.press_key("ctrl+v")

    print("Pasted successfully via GuiPrimitivesController.")


if __name__ == "__main__":
    main()