"""Request queue + autoplay pool with shuffle and repeat."""
from __future__ import annotations
import random
import threading
from .models import Track


class PlayQueue:
    """Requests first, then the autoplay pool (looping). peek() is stable until pop()."""

    def __init__(self, shuffle: bool = True):
        self.requests: list[Track] = []
        self.pool: list[Track] = []
        self.shuffle = shuffle
        self.repeat = "off"  # off | one
        self._i = 0
        self._next_auto: Track | None = None
        self._last: str | None = None
        self.lock = threading.RLock()

    def add_request(self, t: Track) -> int:
        with self.lock:
            self.requests.append(t)
            return len(self.requests)

    def add_pool(self, tracks: list[Track]):
        """Add to the autoplay pool, skipping sources that are already there (reloading a folder is harmless)."""
        with self.lock:
            have = {t.source for t in self.pool}
            for t in tracks:
                if t.source not in have:
                    have.add(t.source)
                    self.pool.append(t)

    def set_shuffle(self, on: bool):
        with self.lock:
            self.shuffle = on
            self._next_auto = None

    def _pick_auto(self) -> Track | None:
        if not self.pool:
            return None
        if self.shuffle:
            c = [t for t in self.pool if t.source != self._last] or self.pool
            return random.choice(c)
        t = self.pool[self._i % len(self.pool)]
        self._i += 1
        return t

    def peek(self) -> Track | None:
        with self.lock:
            if self.requests:
                return self.requests[0]
            if self._next_auto is None:
                self._next_auto = self._pick_auto()
            return self._next_auto

    def pop(self) -> Track | None:
        with self.lock:
            t = self.peek()
            if t is None:
                return None
            if self.requests:
                self.requests.pop(0)
            else:
                self._next_auto = None
            self._last = t.source
            return t

    def get(self, n: int) -> Track | None:
        with self.lock:
            return self.requests[n - 1] if 1 <= n <= len(self.requests) else None

    def remove(self, n: int) -> Track | None:
        with self.lock:
            return self.requests.pop(n - 1) if 1 <= n <= len(self.requests) else None

    def remove_track(self, track: Track) -> bool:
        """Remove exactly this queued request (by identity), however the queue shifted since it was looked up."""
        with self.lock:
            for i, t in enumerate(self.requests):
                if t is track:
                    del self.requests[i]
                    return True
            return False

    def clear(self) -> int:
        with self.lock:
            n = len(self.requests)
            self.requests.clear()
            return n

    def count_by(self, user: str) -> int:
        with self.lock:
            return sum(1 for t in self.requests if t.requester.lower() == user.lower())