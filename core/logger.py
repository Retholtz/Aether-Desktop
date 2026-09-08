"""
Aether Desktop - Central Logging & Diagnostic Subsystem
Provides persistent rotating file logging (logs/aether_debug.log),
real-time log streaming into pywebview UI, memory history buffer,
and performance latency tracking.
"""

import collections
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import threading
from typing import Callable, List, Optional

# Root directory of Aether Desktop
_WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGS_DIR = os.path.join(_WORKSPACE_ROOT, "logs")
_LOG_FILE = os.path.join(_LOGS_DIR, "aether_debug.log")

# In-memory circular buffer for UI initial load (stores last 200 formatted entries)
_LOG_BUFFER = collections.deque(maxlen=200)
_LOG_BUFFER_LOCK = threading.Lock()

# Registered UI streaming callbacks
_UI_CALLBACKS: List[Callable[[dict], None]] = []
_CALLBACKS_LOCK = threading.Lock()


class _UIStreamHandler(logging.Handler):
    """Custom logging handler that buffers logs and emits them to registered UI callbacks."""
    def emit(self, record):
        try:
            msg = self.format(record)
            log_entry = {
                "time": self.formatter.formatTime(record, "%H:%M:%S") if self.formatter else record.asctime,
                "level": record.levelname,
                "name": record.name,
                "message": record.getMessage(),
                "formatted": msg
            }

            with _LOG_BUFFER_LOCK:
                _LOG_BUFFER.append(log_entry)

            with _CALLBACKS_LOCK:
                callbacks = list(_UI_CALLBACKS)

            for cb in callbacks:
                try:
                    cb(log_entry)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)


_LOGGING_INITIALIZED = False
_INIT_LOCK = threading.Lock()


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """
    Initializes root Aether logger with rotating file handler (5MB, 3 backups),
    console stream handler, and UI bridge handler.
    """
    global _LOGGING_INITIALIZED
    with _INIT_LOCK:
        if _LOGGING_INITIALIZED:
            return logging.getLogger("Aether")

        os.makedirs(_LOGS_DIR, exist_ok=True)

        root_logger = logging.getLogger("Aether")
        root_logger.setLevel(level)
        root_logger.propagate = False

        # Formatter with millisecond timestamp, level, module and message
        formatter = logging.Formatter(
            fmt="[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # 1. Rotating File Handler (5 MB max, 3 backups)
        file_handler = RotatingFileHandler(
            _LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # 2. Console Stream Handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        # 3. UI Bridge Stream Handler
        ui_handler = _UIStreamHandler()
        ui_handler.setLevel(logging.DEBUG)
        ui_handler.setFormatter(formatter)
        root_logger.addHandler(ui_handler)

        _LOGGING_INITIALIZED = True
        root_logger.info("Aether Logging Subsystem initialized. Log file: %s", _LOG_FILE)
        return root_logger


def get_logger(name: str) -> logging.Logger:
    """Returns a namespaced logger under the Aether hierarchy (e.g. Aether.Engine)."""
    if not _LOGGING_INITIALIZED:
        setup_logging()
    if name.startswith("Aether.") or name == "Aether":
        return logging.getLogger(name)
    return logging.getLogger(f"Aether.{name}")


def register_ui_log_callback(cb: Callable[[dict], None]):
    """Registers a callback to receive real-time log entries for pywebview UI."""
    with _CALLBACKS_LOCK:
        if cb not in _UI_CALLBACKS:
            _UI_CALLBACKS.append(cb)


def unregister_ui_log_callback(cb: Callable[[dict], None]):
    """Unregisters a UI callback."""
    with _CALLBACKS_LOCK:
        if cb in _UI_CALLBACKS:
            _UI_CALLBACKS.remove(cb)


def get_recent_logs() -> List[dict]:
    """Returns a snapshot of the recent in-memory log buffer."""
    with _LOG_BUFFER_LOCK:
        return list(_LOG_BUFFER)


def clear_memory_logs():
    """Clears the in-memory log buffer."""
    with _LOG_BUFFER_LOCK:
        _LOG_BUFFER.clear()


def get_log_file_path() -> str:
    """Returns the absolute path to the active log file."""
    return _LOG_FILE


def get_logs_directory() -> str:
    """Returns the directory where log files are stored."""
    return _LOGS_DIR


def open_logs_folder() -> bool:
    """Opens the logs directory in Windows Explorer."""
    try:
        os.makedirs(_LOGS_DIR, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(_LOGS_DIR)
            return True
        return False
    except Exception as e:
        get_logger("Logger").error("Failed to open logs directory: %s", e)
        return False

