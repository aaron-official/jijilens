import httpx
import pytest

from jijilens.collect.http import NotFound, PoliteClient, TooManyFailures
from jijilens.config import HttpConfig

CONFIG = HttpConfig(page_delay_seconds=(2, 2), max_retries=2, stop_after_consecutive_failures=2)


def client(handler, sleeps):
    now = [0.0]

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    return PoliteClient(CONFIG, sleep=sleep, clock=lambda: now[0], transport=httpx.MockTransport(handler), log=lambda m: None)


def test_pauses_between_requests():
    sleeps = []
    c = client(lambda request: httpx.Response(200, text="ok"), sleeps)
    c.get("https://jiji.ug/a", delay=(2, 2))
    c.get("https://jiji.ug/b", delay=(2, 2))
    assert sleeps == [2]  # no pause before the very first request


def test_retries_on_503_then_succeeds():
    answers = iter([503, 503, 200])
    sleeps = []
    c = client(lambda request: httpx.Response(next(answers)), sleeps)
    assert c.get("https://jiji.ug/a", delay=(0, 0)).status_code == 200
    assert sleeps[:1] == [5.0]  # first backoff


def test_retries_cloudflare_522():
    answers = iter([522, 200])
    c = client(lambda request: httpx.Response(next(answers)), [])
    assert c.get("https://jiji.ug/a", delay=(0, 0)).status_code == 200


def test_honours_retry_after():
    answers = iter([httpx.Response(429, headers={"Retry-After": "17"}), httpx.Response(200)])
    sleeps = []
    c = client(lambda request: next(answers), sleeps)
    c.get("https://jiji.ug/a", delay=(0, 0))
    assert 17.0 in sleeps


def test_404_is_not_found_and_not_a_failure():
    c = client(lambda request: httpx.Response(404), [])
    for _ in range(5):
        with pytest.raises(NotFound):
            c.get("https://jiji.ug/gone", delay=(0, 0))
    assert c.failures_in_a_row == 0


def test_stops_after_consecutive_failures():
    c = client(lambda request: httpx.Response(403), [])
    with pytest.raises(httpx.HTTPStatusError):
        c.get("https://jiji.ug/a", delay=(0, 0))
    with pytest.raises(TooManyFailures):
        c.get("https://jiji.ug/b", delay=(0, 0))
