"""
Aether Desktop - Elite Dangerous Real-Time Journal Watcher & Telemetry Engine
Monitors Elite Dangerous JSON journal event streams in a non-blocking daemon thread,
maintaining an active in-game state snapshot for LLM conversational context injection.
"""

import os
import glob
import json
import time
import threading
from typing import Dict, Any, Optional


class EliteTelemetryWatcher:
    """Non-blocking tail worker for Elite Dangerous Journal event files."""

    def __init__(self, journal_dir: Optional[str] = None, on_milestone: Optional[Any] = None):
        raw_dir = journal_dir or r"%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous"
        self.journal_dir = os.path.expandvars(raw_dir)
        self.on_milestone = on_milestone
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._current_file: Optional[str] = None
        self._file_handle = None

        # Live State Snapshot
        self.state: Dict[str, Any] = {
            "game": "Elite Dangerous",
            "active": False,
            "star_system": "Unknown",
            "body": "Unknown",
            "station": None,
            "docked": False,
            "supercruise": False,
            "ship_type": "Unknown",
            "ship_name": "Unknown",
            "hull_health": 1.0,
            "shields_up": True,
            "fuel_main": None,
            "cargo_count": 0,
            "legal_status": "Clean",
            "last_event": None,
            "last_updated": 0
        }

    def start(self):
        """Starts background journal polling thread if directory exists."""
        if self.is_running:
            return
        if not os.path.exists(self.journal_dir):
            print(f"[WARN] [TELEMETRY] Journal directory does not exist: {self.journal_dir}")
            return

        self.is_running = True
        self._thread = threading.Thread(target=self._tail_loop, daemon=True, name="EliteJournalWatcher")
        self._thread.start()
        print("[INFO] [TELEMETRY] Started Elite Dangerous Journal Watcher thread.")

    def stop(self):
        """Stops background journal tailing and releases file handles."""
        self.is_running = False
        if self._file_handle:
            try:
                self._file_handle.close()
            except Exception:
                pass
            self._file_handle = None

    def _get_latest_journal_path(self) -> Optional[str]:
        """Finds the most recently modified Journal.*.log file."""
        if not os.path.exists(self.journal_dir):
            return None
        files = glob.glob(os.path.join(self.journal_dir, "Journal.*.log"))
        if not files:
            return None
        return max(files, key=os.path.getmtime)

    def _tail_loop(self):
        """Main loop tailing newest journal log for real-time events."""
        while self.is_running:
            try:
                latest = self._get_latest_journal_path()
                if not latest:
                    time.sleep(2.0)
                    continue

                # If file rotated (new session/game boot), switch handles
                if latest != self._current_file:
                    if self._file_handle:
                        try:
                            self._file_handle.close()
                        except Exception:
                            pass
                    self._current_file = latest
                    self._file_handle = open(self._current_file, "r", encoding="utf-8", errors="replace")
                    self._rehydrate_initial_state()

                line = self._file_handle.readline()
                if line:
                    self._parse_event_line(line.strip())
                else:
                    time.sleep(0.5)

            except Exception as e:
                print(f"[ERROR] [TELEMETRY] Error in tail loop: {e}")
                time.sleep(2.0)

    def _rehydrate_initial_state(self):
        """Scans back through recent events in current journal to seed initial state."""
        if not self._file_handle:
            return
        try:
            self._file_handle.seek(0)
            lines = self._file_handle.readlines()
            for l in lines[-150:]:  # Fast forward through recent 150 events
                self._parse_event_line(l.strip(), is_initial_sync=True)
            self._file_handle.seek(0, os.SEEK_END)
        except Exception as e:
            print(f"[WARN] [TELEMETRY] Rehydration failed: {e}")

    def _parse_event_line(self, line: str, is_initial_sync: bool = False):
        """Extracts and mutates live state snapshot from a single JSON log line."""
        if not line:
            return
        try:
            ev = json.loads(line)
            event_type = ev.get("event")
            self.state["last_event"] = event_type
            self.state["last_updated"] = time.time()
            self.state["active"] = True

            # System / Location
            if event_type in ("Location", "FSDJump", "CarrierJump"):
                self.state["star_system"] = ev.get("StarSystem", self.state["star_system"])
                self.state["body"] = ev.get("Body", self.state["body"])
                self.state["docked"] = ev.get("Docked", False)
                self.state["station"] = ev.get("StationName", None)

            # Docking
            elif event_type == "Docked":
                self.state["docked"] = True
                self.state["station"] = ev.get("StationName")
                self.state["star_system"] = ev.get("StarSystem", self.state["star_system"])

            elif event_type == "Undocked":
                self.state["docked"] = False
                self.state["station"] = None

            # Supercruise
            elif event_type == "SupercruiseEntry":
                self.state["supercruise"] = True
            elif event_type == "SupercruiseExit":
                self.state["supercruise"] = False
                self.state["body"] = ev.get("Body", self.state["body"])

            # Loadout / Ship
            elif event_type == "Loadout":
                self.state["ship_type"] = ev.get("Ship", self.state["ship_type"])
                self.state["ship_name"] = ev.get("ShipName", self.state["ship_name"])
                self.state["hull_health"] = ev.get("HullHealth", 1.0)

            # Shields / Damage
            elif event_type == "ShieldState":
                self.state["shields_up"] = ev.get("ShieldsUp", True)

            # Cargo
            elif event_type == "Cargo":
                self.state["cargo_count"] = ev.get("Count", 0)

            # Live Co-Pilot Milestone Detection (only during live play, not initial back-sync)
            if not is_initial_sync and self.on_milestone and callable(self.on_milestone):
                try:
                    curr_loc = f"{self.state['star_system']}" + (f", {self.state['station']}" if self.state.get("station") else "")
                    if event_type == "MissionAccepted":
                        name = ev.get("Name", "Mission")
                        dest_sys = ev.get("DestinationSystem")
                        dest_stn = ev.get("DestinationStation")
                        dest = f"{dest_sys} ({dest_stn})" if dest_sys and dest_stn else (dest_sys or "Current System")
                        reward = ev.get("Reward", 0)
                        summary = f"Accepted mission: {name} | Target: {dest}" + (f" | Reward: {reward:,} CR" if reward else "")
                        self.on_milestone(category="mission", summary=summary, location=curr_loc, details=line)
                    elif event_type == "MissionRedirected":
                        name = ev.get("Name", "Mission")
                        new_sys = ev.get("NewDestinationSystem", "")
                        new_stn = ev.get("NewDestinationStation", "")
                        summary = f"Mission redirect: {name} -> New destination: {new_sys} ({new_stn})"
                        self.on_milestone(category="mission", summary=summary, location=curr_loc, details=line)
                    elif event_type == "MissionCompleted":
                        name = ev.get("Name", "Mission")
                        reward = ev.get("Reward", 0)
                        summary = f"Completed mission: {name}" + (f" | Earned: {reward:,} CR" if reward else "")
                        self.on_milestone(category="mission", summary=summary, location=curr_loc, details=line)
                    elif event_type in ("CodexEntry", "FSSSignalDiscovered"):
                        sub_name = ev.get("Name_Localised") or ev.get("SignalName") or ev.get("Name")
                        if sub_name:
                            summary = f"Discovery recorded: {sub_name} in system {self.state['star_system']}"
                            self.on_milestone(category="discovery", summary=summary, location=curr_loc, details=line)
                    elif event_type == "EngineerCraft":
                        bp = ev.get("BlueprintName", "Modification")
                        level = ev.get("Level", 1)
                        slot = ev.get("Slot", "Module")
                        summary = f"Engineering upgrade: {bp} (Grade {level}) on {slot}"
                        self.on_milestone(category="intel", summary=summary, location=curr_loc, details=line)
                    elif event_type in ("Interdicted", "EscapeInterdiction"):
                        by_who = ev.get("Interdictor", "Hostile ship")
                        escaped = event_type == "EscapeInterdiction" or ev.get("IsPlayer", False) is False
                        status_text = "successfully evaded" if escaped else "pulled from supercruise by"
                        summary = f"Interdiction: {status_text} {by_who}"
                        self.on_milestone(category="warning", summary=summary, location=curr_loc, details=line)
                except Exception:
                    pass

        except json.JSONDecodeError:
            pass

    def get_summary_prompt_context(self) -> str:
        """Formats the current telemetry snapshot for LLM system context."""
        if not self.state.get("active"):
            return "Active Game: Elite Dangerous (Waiting for live telemetry link...)"

        st = self.state
        status_desc = f"Docked at {st['station']}" if st['docked'] else ("in Supercruise" if st['supercruise'] else "in Normal Space")
        return (
            f"[LIVE GAME TELEMETRY: Elite Dangerous]\n"
            f"- Location: {st['star_system']} (Near: {st['body']}) | Status: {status_desc}\n"
            f"- Ship: {st['ship_name']} ({st['ship_type']}) | Hull: {int(st['hull_health']*100)}% | Shields: {'ONLINE' if st['shields_up'] else 'OFFLINE'}\n"
            f"- Cargo Hold: {st['cargo_count']} items\n"
            f"- Last In-Game Event: {st['last_event']}"
        )
