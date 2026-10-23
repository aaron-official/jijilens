"""Usage limits, so a public demo can't run up the hosting bill.

Each visitor gets a few new photos a minute (upload + detection, the costly
part) and more searches (cheap once a photo's objects are embedded). A daily
cap on photos across all visitors stops someone who keeps changing IP.

Counts live in memory. That suits the deployment: a single machine, and a
restart only resets the counts.

    JIJILENS_PHOTOS_PER_MINUTE    per visitor (default 1)
    JIJILENS_SEARCHES_PER_MINUTE  per visitor (default 20)
    JIJILENS_PHOTOS_PER_DAY       all visitors, per UTC day (default 300; 0 = no cap)
    JIJILENS_CLIENT_IP_HEADER     header with the visitor's IP behind a proxy,
                                  e.g. fly-client-ip on Fly.io (default: none)
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import HTTPException, Request

WINDOW_SECONDS = 60.0
MAX_TRACKED_CLIENTS = 10_000


@dataclass(frozen=True)
class Limits:
    photos_per_minute: int = 1
    searches_per_minute: int = 20
    photos_per_day: int = 300
    client_ip_header: str | None = None

    @classmethod
    def from_env(cls) -> "Limits":
        env = os.environ
        return cls(
            photos_per_minute=int(env.get("JIJILENS_PHOTOS_PER_MINUTE", cls.photos_per_minute)),
            searches_per_minute=int(env.get("JIJILENS_SEARCHES_PER_MINUTE", cls.searches_per_minute)),
            photos_per_day=int(env.get("JIJILENS_PHOTOS_PER_DAY", cls.photos_per_day)),
            client_ip_header=env.get("JIJILENS_CLIENT_IP_HEADER") or None,
        )


class RateLimiter:
    def __init__(
        self,
        limits: Limits,
        *,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], str] = lambda: datetime.now(timezone.utc).date().isoformat(),
    ):
        self.limits = limits
        self._clock = clock
        self._today = today
        self._lock = threading.Lock()
        self._events: dict[tuple[str, str], deque[float]] = {}
        self._day = ""
        self._photos_today = 0

    def client(self, request: Request) -> str:
        header = self.limits.client_ip_header
        if header and request.headers.get(header):
            return request.headers[header].split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def photo(self, request: Request) -> None:
        """A new photo: per-visitor and daily limits. Raises 429 when over."""
        with self._lock:
            self._check(self.client(request), "photo", self.limits.photos_per_minute, "new photo")
            if self.limits.photos_per_day:
                day = self._today()
                if day != self._day:
                    self._day, self._photos_today = day, 0
                if self._photos_today >= self.limits.photos_per_day:
                    raise HTTPException(
                        429, "The demo has reached its daily limit of photos. Please come back tomorrow.",
                    )
                self._photos_today += 1
            self._record(self.client(request), "photo")

    def search(self, request: Request) -> None:
        with self._lock:
            client = self.client(request)
            self._check(client, "search", self.limits.searches_per_minute, "search")
            self._record(client, "search")

    def _check(self, client: str, kind: str, per_minute: int, noun: str) -> None:
        if per_minute <= 0:
            return
        events = self._events.get((client, kind))
        now = self._clock()
        if events is None:
            return
        while events and now - events[0] >= WINDOW_SECONDS:
            events.popleft()
        if len(events) >= per_minute:
            wait = math.ceil(WINDOW_SECONDS - (now - events[0]))
            plural = "" if per_minute == 1 else "es" if noun.endswith("h") else "s"
            raise HTTPException(
                429,
                f"Limit reached: {per_minute} {noun}{plural} a minute. Try again in {wait} s.",
                headers={"Retry-After": str(wait)},
            )

    def _record(self, client: str, kind: str) -> None:
        if len(self._events) > MAX_TRACKED_CLIENTS:  # forget quiet visitors before tracking more
            now = self._clock()
            self._events = {k: v for k, v in self._events.items() if v and now - v[-1] < WINDOW_SECONDS}
        self._events.setdefault((client, kind), deque()).append(self._clock())
