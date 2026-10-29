from __future__ import annotations

import threading
import time
from datetime import datetime, timezone


class Cancelled(Exception):
    pass


class CaptureControl:
    """Thread-safe cancellation and a pause-excluding monotonic live clock."""

    def __init__(self):
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._paused_at: float | None = None
        self._paused_total = 0.0
        self._started = time.monotonic()
        self._history = []

    def begin(self):
        """Start acquisition timing after source preparation/countdown."""
        with self._lock:
            self._started = time.monotonic()
            self._paused_total = 0.0
            if self._paused_at is not None:
                self._paused_at = self._started

    def history(self):
        with self._lock:
            return list(self._history)

    @property
    def stopped(self):
        return self._stop.is_set()

    @property
    def paused(self):
        with self._lock:
            return self._paused_at is not None

    def stop(self):
        self._stop.set()

    def pause(self):
        with self._lock:
            if self._paused_at is None:
                self._paused_at = time.monotonic()
                self._history.append({'type': 'pause', 'at': datetime.now(timezone.utc).isoformat()})

    def resume(self):
        with self._lock:
            if self._paused_at is not None:
                self._paused_total += time.monotonic() - self._paused_at
                self._paused_at = None
                self._history.append({'type': 'resume', 'at': datetime.now(timezone.utc).isoformat()})

    def elapsed(self):
        with self._lock:
            now = self._paused_at if self._paused_at is not None else time.monotonic()
            return now - self._started - self._paused_total

    def checkpoint(self):
        while self.paused and not self.stopped:
            self._stop.wait(0.03)
        if self.stopped:
            raise Cancelled("Capture stopped by user")

    def wait_until(self, elapsed: float):
        while self.elapsed() < elapsed:
            self.checkpoint()
            self._stop.wait(min(0.03, max(0, elapsed - self.elapsed())))
        self.checkpoint()
