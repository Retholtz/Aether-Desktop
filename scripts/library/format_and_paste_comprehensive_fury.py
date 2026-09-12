import time
import win32clipboard
import win32con
import win32gui
from core.screen_stream import ensure_thread_desktop
from tools.gui_primitives import GuiPrimitivesController
from tools.os_controls import bring_hwnd_to_foreground, find_hwnd_by_query

ensure_thread_desktop()


def format_windows_html_clipboard(html_fragment: str) -> bytes:
    header_template = (
        "Version:0.9\r\n"
        "StartHTML:{start_html:010d}\r\n"
        "EndHTML:{end_html:010d}\r\n"
        "StartFragment:{start_frag:010d}\r\n"
        "EndFragment:{end_frag:010d}\r\n"
    )
    prefix = "<html><body>\r\n<!--StartFragment-->"
    suffix = "<!--EndFragment-->\r\n</body></html>"

    dummy = header_template.format(start_html=0, end_html=0, start_frag=0, end_frag=0)
    header_len = len(dummy.encode("utf-8"))
    start_html = header_len
    start_frag = header_len + len(prefix.encode("utf-8"))
    end_frag = start_frag + len(html_fragment.encode("utf-8"))
    end_html = end_frag + len(suffix.encode("utf-8"))

    header = header_template.format(
        start_html=start_html,
        end_html=end_html,
        start_frag=start_frag,
        end_frag=end_frag,
    )
    return (header + prefix + html_fragment + suffix).encode("utf-8")


def set_clipboard_payload(
    html_content: str, text_content: str, max_retries: int = 5, retry_delay: float = 0.05
) -> None:
    cf_html = win32clipboard.RegisterClipboardFormat("HTML Format")
    html_data = format_windows_html_clipboard(html_content)

    for attempt in range(max_retries):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(cf_html, html_data)
                win32clipboard.SetClipboardText(text_content, win32con.CF_UNICODETEXT)
                return
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(retry_delay)


def wait_for_foreground(hwnd: int, timeout: float = 1.0, poll_interval: float = 0.02) -> bool:
    start_time = time.perf_counter()
    while time.perf_counter() - start_time < timeout:
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        time.sleep(poll_interval)
    return win32gui.GetForegroundWindow() == hwnd


def paste_formatted_template(
    html_body: str,
    plain_text_fallback: str,
    target_queries: list[str] = None,
) -> None:
    if target_queries is None:
        target_queries = ["Google Docs", "chrome"]

    target_hwnd = None
    for query in target_queries:
        target_hwnd = find_hwnd_by_query(query)
        if target_hwnd:
            break

    if target_hwnd:
        bring_hwnd_to_foreground(target_hwnd)
        wait_for_foreground(target_hwnd, timeout=0.8)

    set_clipboard_payload(html_body, plain_text_fallback)

    gui = GuiPrimitivesController()
    gui.press_key("ctrl+v")


HTML_DOC = """
<div style="font-family: 'Segoe UI', Arial, sans-serif; color: #1e293b; line-height: 1.5;">
  <h1 style="color: #b91c1c; border-bottom: 2px solid #b91c1c; padding-bottom: 6px; margin-bottom: 12px;">
    World of Warcraft: Fury Warrior Meta Template (The War Within)
  </h1>
  <p style="font-size: 11pt; color: #475569; margin-top: 0;">
    <strong>Role:</strong> Melee DPS &bull; <strong>Armor:</strong> Plate &bull; <strong>Weapons:</strong> Dual Two-Handed Weapons (Titan's Grip) &bull; <strong>Primary Resource:</strong> Rage
  </p>

  <h2 style="color: #991b1b; margin-top: 20px; margin-bottom: 8px;">1. Meta Build Overview</h2>
  <table border="1" style="border-collapse: collapse; width: 100%; font-size: 10.5pt; margin-bottom: 16px;">
    <thead>
      <tr style="background-color: #f1f5f9;">
        <th style="border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left;">Category</th>
        <th style="border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left;">Meta Recommendation</th>
        <th style="border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left;">Strategic Context</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold;">Hero Talent Choice</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;"><strong>Slayer</strong> (Raid / High Single Target & Burst)<br><strong>Mountain Thane</strong> (Cleave / M+ AOE / Lightning)</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Slayer optimizes Bladestorm and Execute phases; Mountain Thane provides heavy passive AoE and Thunder Blast procs.</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold;">Stat Priority</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;"><strong>Strength &gt; Mastery &gt; Haste &gt; Versatility &gt; Critical Strike</strong></td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Item level / Strength is king. Mastery (Unshackled Fury) multiplies all damage while Enraged. Haste smoothes rage generation.</td>
      </tr>
      <tr>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold;">Primary Goal</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;"><strong>Near 100% Enrage Uptime</strong></td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Enrage grants bonus attack speed, movement speed, and massive damage scaling.</td>
      </tr>
    </tbody>
  </table>

  <h2 style="color: #991b1b; margin-top: 20px; margin-bottom: 8px;">2. Hero Talent Breakdown: Slayer vs. Mountain Thane</h2>
  <table border="1" style="border-collapse: collapse; width: 100%; font-size: 10.5pt; margin-bottom: 16px;">
    <thead>
      <tr style="background-color: #f1f5f9;">
        <th style="border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left;">Hero Tree</th>
        <th style="border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left;">Primary Strengths</th>
        <th style="border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left;">Key Mechanics & Abilities</th>
        <th style="border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left;">Optimal Content</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold; color: #b91c1c;">Slayer</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Exceptional burst damage, lethal Execute phase, and high single-target scaling.</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Slayer's Dominance, empowered Bladestorm, uninterrupted movement during channel.</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Raid Bosses, Tyrannical M+, Priority Target Burst.</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold; color: #0369a1;">Mountain Thane</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Continuous sustained AoE, high Rage generation, added passive durability.</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Lightning Strikes, Thunder Blast (replaces Whirlwind/Thunder Clap), Stormstrike cleave.</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Fortified M+ Trash Packs, Delves, Heavy AoE Encounters.</td>
      </tr>
    </tbody>
  </table>

  <h2 style="color: #991b1b; margin-top: 20px; margin-bottom: 8px;">3. Core Rotation Priority</h2>
  <table border="1" style="border-collapse: collapse; width: 100%; font-size: 10.5pt; margin-bottom: 16px;">
    <thead>
      <tr style="background-color: #f1f5f9;">
        <th style="border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left;">Priority</th>
        <th style="border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left;">Single Target Execution</th>
        <th style="border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left;">Multi-Target (AoE / Cleave) Execution</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold;">1</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Cast <strong>Rampage</strong> if not Enraged or at/near Rage cap (80+ Rage).</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Maintain <strong>Whirlwind / Meat Cleaver</strong> buff so next 4 single target attacks hit all targets.</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold;">2</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Pop major offensive cooldowns: <strong>Recklessness</strong> and <strong>Avatar</strong>.</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Cast <strong>Bladestorm / Odyn's Fury / Ravager</strong> on pack pulls while Enraged.</td>
      </tr>
      <tr>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold;">3</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Cast <strong>Execute</strong> (or Sudden Death procs) on cooldown.</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Cast <strong>Thunder Blast</strong> (if Mountain Thane) on proc.</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold;">4</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Cast <strong>Bloodbath / Bloodthirst</strong> or <strong>Crushing Blow / Raging Blow</strong> on charge cooldown.</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Cast <strong>Rampage</strong> to cleave and sustain Enrage across the pull.</td>
      </tr>
      <tr>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold;">5</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Filler: Use <strong>Whirlwind / Slam</strong> only if starved of Rage and all abilities on CD.</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Refresh <strong>Meat Cleaver</strong> before spending high-damage abilities.</td>
      </tr>
    </tbody>
  </table>

  <h2 style="color: #991b1b; margin-top: 20px; margin-bottom: 8px;">4. Consumables, Gems & Enchants</h2>
  <table border="1" style="border-collapse: collapse; width: 100%; font-size: 10.5pt;">
    <thead>
      <tr style="background-color: #f1f5f9;">
        <th style="border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left;">Slot / Item</th>
        <th style="border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left;">Recommended Choice</th>
        <th style="border: 1px solid #cbd5e1; padding: 8px 12px; text-align: left;">Notes</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold;">Flask / Phial</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Flask of Tempered Swiftness / Aggression</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Focus on Haste or pure secondary stat balance.</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold;">Combat Potion</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Tempered Potion</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Pop alongside Recklessness and Bloodlust/Heroism.</td>
      </tr>
      <tr>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold;">Weapon Enchants</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Authority of Radiant Power / Council's Guile</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Enchant both two-handed weapons for maximum dual-wield output.</td>
      </tr>
      <tr style="background-color: #f8fafc;">
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px; font-weight: bold;">Gems</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Masterful Emerald / Quick Onyx & Primary Stat Diamond</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px 12px;">Prioritize Mastery + Haste hybrid gems.</td>
      </tr>
    </tbody>
  </table>
</div>
"""

PLAIN_TEXT = (
    "World of Warcraft: Fury Warrior Meta Template (The War Within)\n"
    "Role: Melee DPS | Weapons: Dual Two-Handed Weapons | Resource: Rage\n"
    "Stat Priority: Strength > Mastery > Haste > Versatility > Critical Strike"
)

if __name__ == "__main__":
    paste_formatted_template(HTML_DOC, PLAIN_TEXT)