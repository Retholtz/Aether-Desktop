import time
import random
import threading
from enum import Enum
from typing import Optional, Callable


class ConnectionState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"


class LiveConnectionManager:
    def __init__(
        self,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        jitter_factor: float = 0.2,
        max_retries: int = 10
    ):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter_factor = jitter_factor
        self.max_retries = max_retries

        self.state = ConnectionState.DISCONNECTED
        self.retry_count = 0
        self.last_connected_ts = 0.0
        self.last_heartbeat_ts = 0.0
        self._lock = threading.Lock()

    def set_connected(self):
        with self._lock:
            self.state = ConnectionState.CONNECTED
            self.retry_count = 0
            self.last_connected_ts = time.time()
            self.last_heartbeat_ts = time.time()
            print("[INFO] [CONNECTION] Live WebSocket established and healthy.")

    def set_disconnected(self, reason: str = "Unknown"):
        with self._lock:
            prev_state = self.state
            self.state = ConnectionState.DISCONNECTED
            print(f"[WARN] [CONNECTION] Live WebSocket disconnected ({reason}). Previous state: {prev_state.value}")

    def should_attempt_reconnect(self) -> bool:
        with self._lock:
            return self.retry_count < self.max_retries and self.state != ConnectionState.CONNECTED

    def compute_next_backoff(self) -> float:
        """Computes exponential backoff delay with random jitter."""
        with self._lock:
            self.state = ConnectionState.RECONNECTING
            self.retry_count += 1
            
            delay = min(self.max_delay, self.base_delay * (2 ** (self.retry_count - 1)))
            jitter = delay * self.jitter_factor * (random.random() * 2 - 1)
            final_delay = max(self.base_delay, delay + jitter)
            
            print(f"[INFO] [CONNECTION] Reconnect attempt {self.retry_count}/{self.max_retries} scheduled in {final_delay:.2f}s")
            return final_delay

    def record_activity(self):
        """Marks incoming/outgoing packet activity to refresh liveness."""
        with self._lock:
            self.last_heartbeat_ts = time.time()
