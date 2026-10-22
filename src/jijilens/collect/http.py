"""A polite HTTP client: one request at a time, a random pause before each,
backoff on 429/5xx, and a hard stop when the site keeps failing (blocked, or
down) so we never hammer it."""

from __future__ import annotations

import random
import time
from typing import Callable

import httpx

from jijilens.config import HttpConfig

BACKOFF_BASE_SECONDS = 5.0
BACKOFF_MAX_SECONDS = 120.0


class NotFound(Exception):
    """404 or 410: the ad or image is gone. Not a failure of the site."""


class TooManyFailures(Exception):
    """Too many requests in a row failed; stop the run."""


class PoliteClient:
    def __init__(
        self,
        config: HttpConfig,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        transport: httpx.BaseTransport | None = None,
        log: Callable[[str], None] = print,
    ):
        self.config = config
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        self._log = log
        self._last_request: float | None = None
        self.failures_in_a_row = 0
        self._http = httpx.Client(
            headers={"User-Agent": config.user_agent, "Accept-Language": "en"},
            timeout=config.timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get(self, url: str, *, delay: tuple[float, float], accept: str | None = None) -> httpx.Response:
        """GET with retries. Raises NotFound, TooManyFailures, or the last httpx error."""
        headers = {"Accept": accept} if accept else None
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            if attempt:
                wait = self._backoff(attempt, last_error)
                self._log(f"    retry {attempt}/{self.config.max_retries} in {wait:.0f}s: {last_error}")
                self._sleep(wait)
            self._wait_turn(delay)
            try:
                response = self._http.get(url, headers=headers)
            except httpx.TransportError as exc:
                last_error = exc
                continue
            if response.status_code in (404, 410):
                self.failures_in_a_row = 0
                raise NotFound(url)
            # Every 5xx, including Cloudflare's 520-524 (origin unreachable or slow).
            if response.status_code == 429 or response.status_code >= 500:
                last_error = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:  # other 4xx: retrying won't help
                last_error = exc
                break
            self.failures_in_a_row = 0
            return response

        self.failures_in_a_row += 1
        if self.failures_in_a_row >= self.config.stop_after_consecutive_failures:
            raise TooManyFailures(
                f"{self.failures_in_a_row} requests in a row failed (blocked or site down?); last: {last_error}"
            )
        assert last_error is not None
        raise last_error

    def _wait_turn(self, delay: tuple[float, float]) -> None:
        pause = self._rng.uniform(*delay)
        if self._last_request is not None:
            remaining = pause - (self._clock() - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request = self._clock()

    def _backoff(self, attempt: int, error: Exception | None) -> float:
        if isinstance(error, httpx.HTTPStatusError):
            retry_after = error.response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                return min(float(retry_after), BACKOFF_MAX_SECONDS)
        return min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_MAX_SECONDS)
