import pytest
from fastapi import HTTPException
from starlette.requests import Request

from jijilens.api.limits import Limits, RateLimiter


def request(ip="1.2.3.4", headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "client": (ip, 1234), "headers": raw})


def limiter(limits, now, day=lambda: "2026-10-24"):
    return RateLimiter(limits, clock=lambda: now[0], today=day)


def test_per_visitor_photos_per_minute():
    now = [0.0]
    rl = limiter(Limits(photos_per_minute=1, photos_per_day=0), now)
    rl.photo(request())
    with pytest.raises(HTTPException) as exc:
        rl.photo(request())
    assert exc.value.status_code == 429 and exc.value.headers["Retry-After"] == "60"
    rl.photo(request("5.6.7.8"))  # another visitor is not affected
    now[0] = 45.0
    with pytest.raises(HTTPException) as exc:
        rl.photo(request())
    assert exc.value.headers["Retry-After"] == "15"
    now[0] = 60.0
    rl.photo(request())  # a minute later, allowed again


def test_daily_cap_across_visitors_resets_each_day():
    now, day = [0.0], ["2026-10-24"]
    rl = limiter(Limits(photos_per_minute=0, photos_per_day=2), now, lambda: day[0])
    rl.photo(request("1.1.1.1"))
    rl.photo(request("2.2.2.2"))
    with pytest.raises(HTTPException, match="daily limit"):
        rl.photo(request("3.3.3.3"))
    day[0] = "2026-10-25"
    rl.photo(request("3.3.3.3"))


def test_searches_per_minute_message():
    rl = limiter(Limits(searches_per_minute=2), [0.0])
    rl.search(request())
    rl.search(request())
    with pytest.raises(HTTPException, match="2 searches a minute"):
        rl.search(request())


def test_client_ip_from_proxy_header_only_when_configured():
    behind_proxy = request("10.0.0.1", {"Fly-Client-IP": "41.210.1.2"})
    assert limiter(Limits(client_ip_header="fly-client-ip"), [0.0]).client(behind_proxy) == "41.210.1.2"
    assert limiter(Limits(), [0.0]).client(behind_proxy) == "10.0.0.1"  # a header anyone could send
