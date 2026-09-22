"""
Aether Desktop - Background Startup Automation & Task Monitor Registry
Manages long-term autonomous polling, scheduled scripts, and boot tasks in scripts/startup/
without blocking the main voice and HUD loop.
"""

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STARTUP_DIR = os.path.join(WORKSPACE_ROOT, "scripts", "startup")
REGISTRY_PATH = os.path.join(STARTUP_DIR, "registry.json")


class StartupJobRunner:
    """Dedicated background scheduler and task manager for persistent monitoring jobs."""

    def __init__(
        self,
        engine=None,
        workspace_root: Optional[str] = None,
        registry_path: Optional[str] = None,
        startup_dir: Optional[str] = None,
        check_interval_seconds: int = 30,
    ):
        self.engine = engine
        self.workspace_root = workspace_root or WORKSPACE_ROOT
        self.startup_dir = startup_dir or os.path.join(self.workspace_root, "scripts", "startup")
        self.registry_path = registry_path or os.path.join(self.startup_dir, "registry.json")
        self.check_interval_seconds = check_interval_seconds

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._ensure_infrastructure()

    def _ensure_infrastructure(self):
        """Ensures the scripts/startup/ directory and registry.json exist."""
        os.makedirs(self.startup_dir, exist_ok=True)
        if not os.path.exists(self.registry_path):
            with open(self.registry_path, "w", encoding="utf-8") as f:
                json.dump({"monitors": []}, f, indent=2)

    def load_registry(self) -> Dict[str, Any]:
        """Loads and returns the current monitor registry from disk."""
        with self._lock:
            try:
                if os.path.exists(self.registry_path):
                    with open(self.registry_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and "monitors" in data:
                            return data
            except Exception as e:
                print(f"[WARN] [STARTUP_RUNNER] Failed to read registry: {e}")
            return {"monitors": []}

    def save_registry(self, data: Dict[str, Any]):
        """Persists the registry state to disk."""
        with self._lock:
            try:
                with open(self.registry_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                print(f"[ERROR] [STARTUP_RUNNER] Failed to save registry: {e}")

    def register_task(
        self,
        task_id: str,
        description: str,
        script_name: str,
        interval_minutes: int
    ) -> bool:
        """Registers or updates a scheduled monitoring script in the registry."""
        with self._lock:
            registry = self.load_registry()
            # Canonical relative path relative to workspace or startup_dir
            script_path = os.path.join("scripts", "startup", script_name)

            existing = next((m for m in registry.get("monitors", []) if m.get("task_id") == task_id), None)
            if existing:
                existing["description"] = description
                existing["script_path"] = script_path
                existing["interval_minutes"] = interval_minutes
                existing["enabled"] = True
                existing["last_status"] = "registered"
            else:
                registry.setdefault("monitors", []).append({
                    "task_id": task_id,
                    "description": description,
                    "script_path": script_path,
                    "interval_minutes": interval_minutes,
                    "enabled": True,
                    "last_run_timestamp": 0,
                    "last_status": "registered"
                })

            self.save_registry(registry)
            print(f"[INFO] [STARTUP_RUNNER] Registered monitor: {task_id}")
            return True

    def start(self):
        """Starts the background monitoring loop on Aether boot."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="Aether-StartupScheduler"
        )
        self._thread.start()
        print("[INFO] [STARTUP_RUNNER] Background task scheduler started.")

    def stop(self):
        """Stops the scheduler and waits for the worker thread to exit."""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            self._thread = None
        print("[INFO] [STARTUP_RUNNER] Background task scheduler stopped.")

    def _scheduler_loop(self):
        """Periodic scheduler loop running in background daemon thread."""
        while self._running:
            try:
                registry = self.load_registry()
                now = time.time()

                for monitor in registry.get("monitors", []):
                    if not monitor.get("enabled", False):
                        continue

                    interval_sec = monitor.get("interval_minutes", 60) * 60
                    last_run = monitor.get("last_run_timestamp", 0)

                    if now - last_run >= interval_sec:
                        self._execute_monitor_script(monitor)
            except Exception as e:
                print(f"[ERROR] [STARTUP_RUNNER] Scheduler exception: {e}")

            # Wait check_interval_seconds or wake up immediately if stopped
            if self._stop_event.wait(timeout=self.check_interval_seconds):
                break

    def _resolve_script_path(self, script_path: str) -> str:
        """Resolves absolute or relative script path against workspace or startup dir."""
        if os.path.isabs(script_path) and os.path.exists(script_path):
            return script_path

        cand1 = os.path.join(self.workspace_root, script_path)
        if os.path.exists(cand1):
            return cand1

        cand2 = os.path.join(self.startup_dir, os.path.basename(script_path))
        if os.path.exists(cand2):
            return cand2

        if os.path.exists(script_path):
            return os.path.abspath(script_path)

        return cand1

    def _execute_monitor_script(self, monitor: Dict[str, Any]):
        """Executes a monitor script in an isolated subprocess and handles notifications."""
        raw_path = monitor.get("script_path", "")
        task_id = monitor.get("task_id", "")
        resolved_path = self._resolve_script_path(raw_path)

        if not os.path.exists(resolved_path):
            print(f"[ERROR] [STARTUP_RUNNER] Script not found: {resolved_path}")
            monitor["last_status"] = "missing_file"
            self._update_monitor_status(task_id, "missing_file")
            return

        print(f"[INFO] [STARTUP_RUNNER] Running background job: {task_id}")
        last_run = time.time()
        status = "unknown"

        try:
            result = subprocess.run(
                [sys.executable, resolved_path],
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode == 0:
                status = "success"
                # If script produced output to notify the user, route through engine proactive notification
                if result.stdout and self.engine and hasattr(self.engine, "post_proactive_event"):
                    lines = result.stdout.strip().splitlines()
                    for line in lines:
                        clean_line = line.strip()
                        if clean_line.startswith("[NOTIFY]"):
                            msg = clean_line.replace("[NOTIFY]", "", 1).strip()
                            self.engine.post_proactive_event(f"[{task_id}] {msg}")
            else:
                err_snip = (result.stderr or result.stdout or "")[:100].strip()
                status = f"error: {err_snip}" if err_snip else f"error: returncode {result.returncode}"
        except subprocess.TimeoutExpired:
            status = "timeout_exceeded"
        except Exception as err:
            status = f"exception: {str(err)}"

        monitor["last_run_timestamp"] = last_run
        monitor["last_status"] = status
        self._update_monitor_status(task_id, status, last_run=last_run)

    def _update_monitor_status(self, task_id: str, status: str, last_run: Optional[float] = None):
        """Thread-safe update of monitor status and execution timestamp in registry."""
        with self._lock:
            registry = self.load_registry()
            for m in registry.get("monitors", []):
                if m.get("task_id") == task_id:
                    m["last_status"] = status
                    if last_run is not None:
                        m["last_run_timestamp"] = last_run
                    break
            self.save_registry(registry)

    def run_task_now(self, task_id: str) -> bool:
        """Immediately executes a registered task regardless of its scheduled interval."""
        registry = self.load_registry()
        monitor = next((m for m in registry.get("monitors", []) if m.get("task_id") == task_id), None)
        if not monitor:
            return False
        self._execute_monitor_script(monitor)
        return True

