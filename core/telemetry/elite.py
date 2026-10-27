"""
core/telemetry/elite.py - Elite Dangerous Telemetry & Status Adapter.

Monitors Elite Dangerous JSON journal events and Status.json in a non-blocking daemon thread.
Maintains state flags (landing gear, hardpoints, shields, docked, etc.) and milestone detections.
"""

import os
import glob
import json
import time
import threading
from typing import Dict, Any, Optional, Callable

from core.telemetry.base import BaseTelemetryAdapter
from core.logger import get_logger

logger = get_logger("EliteTelemetry")

STATUS_FLAGS = {
    "docked": 0x00000001,
    "landed": 0x00000002,
    "landing_gear_down": 0x00000004,
    "shields_up": 0x00000008,
    "supercruise": 0x00000010,
    "flight_assist_off": 0x00000020,
    "hardpoints_deployed": 0x00000040,
    "in_wing": 0x00000080,
    "lights_on": 0x00000100,
    "cargo_scoop_open": 0x00000200,
    "silent_running": 0x00000400,
    "scooping_fuel": 0x00080000,
    "srv_handbrake": 0x00001000,
    "srv_turret": 0x00002000,
    "srv_under_ship": 0x00004000,
    "srv_drive_assist": 0x00008000,
    "fsd_mass_locked": 0x00010000,
    "fsd_charging": 0x00020000,
    "fsd_cooldown": 0x00040000,
    "low_fuel": 0x00080000,
    "overheating": 0x00100000,
    "has_lat_long": 0x00200000,
    "is_in_danger": 0x00400000,
    "being_interdicted": 0x00800000,
    "in_main_ship": 0x01000000,
    "in_fighter": 0x02000000,
    "in_srv": 0x04000000,
    "analysis_mode": 0x08000000,
    "night_vision": 0x10000000,
    "fsd_jump": 0x40000000,
}


class EliteTelemetryAdapter(BaseTelemetryAdapter):
    """Adapter tailing Elite Dangerous Journal.*.log and polling Status.json."""

    def __init__(
        self,
        game_id: str = "elite_dangerous",
        profile: Optional[Dict[str, Any]] = None,
        on_milestone: Optional[Callable[[str, str, str, str], None]] = None,
        journal_dir: Optional[str] = None
    ):
        super().__init__(game_id, profile=profile, on_milestone=on_milestone)
        raw_dir = (
            journal_dir
            or (self.profile.get("telemetry") or {}).get("dir")
            or self.profile.get("journal_dir")
            or self.profile.get("telemetry_dir")
            or r"%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous"
        )
        self.journal_dir = os.path.expandvars(raw_dir)
        self.status_file_path = os.path.join(self.journal_dir, "Status.json")

        self._thread: Optional[threading.Thread] = None
        self._current_file: Optional[str] = None
        self._file_handle = None
        self._lock = threading.Lock()

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
            "last_updated": 0,
            "pips": [2, 2, 2],
            "gui_focus": 0,
        }

        # Boolean state flags for macro conditions
        self.flags: Dict[str, bool] = {
            "docked": False,
            "landed": False,
            "landing_gear_down": False,
            "shields_up": True,
            "supercruise": False,
            "hardpoints_deployed": False,
            "cargo_scoop_open": False,
            "lights_on": False,
            "night_vision": False,
            "silent_running": False,
            "in_fighter": False,
            "in_srv": False,
            "in_main_ship": True,
        }

    def start(self) -> None:
        """Starts background journal & status polling thread."""
        if self.is_running:
            return
        if not os.path.exists(self.journal_dir):
            logger.warning(f"Journal directory does not exist: {self.journal_dir}")
            return

        self.is_running = True
        self._thread = threading.Thread(target=self._tail_loop, daemon=True, name="EliteTelemetryWatcher")
        self._thread.start()
        logger.info("Started Elite Dangerous Journal & Status Watcher thread.")

    def stop(self) -> None:
        """Stops background journal tailing and releases file handles."""
        self.is_running = False
        if self._file_handle:
            try:
                self._file_handle.close()
            except Exception:
                pass
            self._file_handle = None

    def is_active(self) -> bool:
        with self._lock:
            return bool(self.state.get("active"))

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.state)

    def get_state_flags(self) -> Dict[str, bool]:
        with self._lock:
            return dict(self.flags)

    def _get_latest_journal_path(self) -> Optional[str]:
        if not os.path.exists(self.journal_dir):
            return None
        files = glob.glob(os.path.join(self.journal_dir, "Journal.*.log"))
        if not files:
            return None
        return max(files, key=os.path.getmtime)

    def _tail_loop(self) -> None:
        last_status_poll = 0.0
        while self.is_running:
            try:
                now = time.time()
                # 1. Poll Status.json every 200ms for instant state flags
                if now - last_status_poll >= 0.2:
                    self._poll_status_file()
                    last_status_poll = now

                # 2. Tail Journal.*.log for game events
                latest = self._get_latest_journal_path()
                if not latest:
                    time.sleep(1.0)
                    continue

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
                    time.sleep(0.2)

            except Exception as e:
                logger.error(f"Error in telemetry loop: {e}")
                time.sleep(1.0)

    def _poll_status_file(self) -> None:
        """Reads Status.json to extract real-time bitflags, pips, and GUI focus."""
        if not os.path.exists(self.status_file_path):
            return
        try:
            with open(self.status_file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read().strip()
                if not content:
                    return
                data = json.loads(content)

            raw_flags = data.get("Flags", 0)
            with self._lock:
                for flag_name, bitmask in STATUS_FLAGS.items():
                    self.flags[flag_name] = bool(raw_flags & bitmask)

                # Sync flags into state dict
                self.state["docked"] = self.flags.get("docked", False)
                self.state["supercruise"] = self.flags.get("supercruise", False)
                self.state["shields_up"] = self.flags.get("shields_up", True)

                # Pips [sys, eng, wep]
                if "Pips" in data and isinstance(data["Pips"], list) and len(data["Pips"]) == 3:
                    self.state["pips"] = [p / 2.0 for p in data["Pips"]]

                # Gui Focus (0: none, 1: right, 2: left, 3: comms, 4: role panel)
                self.state["gui_focus"] = data.get("GuiFocus", 0)
                self.state["active"] = True
                self.state["last_updated"] = time.time()
        except Exception:
            pass

    def _rehydrate_initial_state(self) -> None:
        """Fast-forwards through recent journal events to seed system and ship info."""
        if not self._file_handle:
            return
        try:
            self._file_handle.seek(0)
            lines = self._file_handle.readlines()
            for l in lines[-150:]:
                self._parse_event_line(l.strip(), is_initial_sync=True)
            self._file_handle.seek(0, os.SEEK_END)
        except Exception as e:
            logger.warning(f"Rehydration failed: {e}")

    def _parse_event_line(self, line: str, is_initial_sync: bool = False) -> None:
        if not line:
            return
        try:
            ev = json.loads(line)
            event_type = ev.get("event")
            with self._lock:
                self.state["last_event"] = event_type
                self.state["last_updated"] = time.time()
                self.state["active"] = True

                if event_type in ("Location", "FSDJump", "CarrierJump"):
                    self.state["star_system"] = ev.get("StarSystem", self.state["star_system"])
                    self.state["body"] = ev.get("Body", self.state["body"])
                    self.state["docked"] = ev.get("Docked", False)
                    self.state["station"] = ev.get("StationName", None)

                elif event_type == "Docked":
                    self.state["docked"] = True
                    self.state["station"] = ev.get("StationName")
                    self.state["star_system"] = ev.get("StarSystem", self.state["star_system"])

                elif event_type == "Undocked":
                    self.state["docked"] = False
                    self.state["station"] = None

                elif event_type == "SupercruiseEntry":
                    self.state["supercruise"] = True
                elif event_type == "SupercruiseExit":
                    self.state["supercruise"] = False
                    self.state["body"] = ev.get("Body", self.state["body"])

                elif event_type == "Loadout":
                    self.state["ship_type"] = ev.get("Ship", self.state["ship_type"])
                    self.state["ship_name"] = ev.get("ShipName", self.state["ship_name"])
                    self.state["hull_health"] = ev.get("HullHealth", 1.0)

                elif event_type == "ShieldState":
                    self.state["shields_up"] = ev.get("ShieldsUp", True)
                    self.flags["shields_up"] = self.state["shields_up"]

                elif event_type == "Cargo":
                    self.state["cargo_count"] = ev.get("Count", 0)

            # Milestones (only during live play)
            if not is_initial_sync and self.on_milestone and callable(self.on_milestone):
                curr_loc = f"{self.state['star_system']}" + (f", {self.state['station']}" if self.state.get("station") else "")
                if event_type == "MissionAccepted":
                    name = ev.get("Name", "Mission")
                    dest_sys = ev.get("DestinationSystem")
                    dest_stn = ev.get("DestinationStation")
                    dest = f"{dest_sys} ({dest_stn})" if dest_sys and dest_stn else (dest_sys or "Current System")
                    reward = ev.get("Reward", 0)
                    summary = f"Accepted mission: {name} | Target: {dest}" + (f" | Reward: {reward:,} CR" if reward else "")
                    self.on_milestone("mission", summary, curr_loc, line)
                elif event_type == "MissionRedirected":
                    name = ev.get("Name", "Mission")
                    new_sys = ev.get("NewDestinationSystem", "")
                    new_stn = ev.get("NewDestinationStation", "")
                    summary = f"Mission redirect: {name} -> New destination: {new_sys} ({new_stn})"
                    self.on_milestone("mission", summary, curr_loc, line)
                elif event_type == "MissionCompleted":
                    name = ev.get("Name", "Mission")
                    reward = ev.get("Reward", 0)
                    summary = f"Completed mission: {name}" + (f" | Earned: {reward:,} CR" if reward else "")
                    self.on_milestone("mission", summary, curr_loc, line)
                elif event_type in ("CodexEntry", "FSSSignalDiscovered"):
                    sub_name = ev.get("Name_Localised") or ev.get("SignalName") or ev.get("Name")
                    if sub_name:
                        summary = f"Discovery recorded: {sub_name} in system {self.state['star_system']}"
                        self.on_milestone("discovery", summary, curr_loc, line)
                elif event_type == "EngineerCraft":
                    bp = ev.get("BlueprintName", "Modification")
                    level = ev.get("Level", 1)
                    slot = ev.get("Slot", "Module")
                    summary = f"Engineering upgrade: {bp} (Grade {level}) on {slot}"
                    self.on_milestone("intel", summary, curr_loc, line)
                elif event_type in ("Interdicted", "EscapeInterdiction"):
                    by_who = ev.get("Interdictor", "Hostile ship")
                    escaped = event_type == "EscapeInterdiction" or ev.get("IsPlayer", False) is False
                    status_text = "successfully evaded" if escaped else "pulled from supercruise by"
                    summary = f"Interdiction: {status_text} {by_who}"
                    self.on_milestone("warning", summary, curr_loc, line)

        except json.JSONDecodeError:
            pass

    def get_summary_prompt_context(self) -> str:
        with self._lock:
            if not self.state.get("active"):
                return "Active Game: Elite Dangerous (Waiting for live telemetry link...)"

            st = self.state
            fl = self.flags
            status_desc = (
                f"Docked at {st['station']}" if st['docked']
                else (f"Landed on {st['body']}" if fl.get("landed")
                else ("in Supercruise" if st['supercruise'] else "in Normal Space"))
            )
            gear_str = "DOWN" if fl.get("landing_gear_down") else "UP"
            hardpoints_str = "DEPLOYED" if fl.get("hardpoints_deployed") else "RETRACTED"
            scoop_str = "OPEN" if fl.get("cargo_scoop_open") else "CLOSED"

            return (
                f"[LIVE GAME TELEMETRY: Elite Dangerous]\n"
                f"- Location: {st['star_system']} (Near: {st['body']}) | Status: {status_desc}\n"
                f"- Ship: {st['ship_name']} ({st['ship_type']}) | Hull: {int(st['hull_health']*100)}% | Shields: {'ONLINE' if st['shields_up'] else 'OFFLINE'}\n"
                f"- Ship State: Gear: {gear_str} | Hardpoints: {hardpoints_str} | Cargo Scoop: {scoop_str}\n"
                f"- Cargo Hold: {st['cargo_count']} items | Pips (Sys/Eng/Wep): {st['pips']}\n"
                f"- Last In-Game Event: {st['last_event']}"
            )
