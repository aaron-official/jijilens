import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class Wrap:
    """Marks a value Nuxt wraps in a Vue reducer, e.g. Wrap("Reactive", {...})."""

    def __init__(self, tag, value):
        self.tag, self.value = tag, value


def devalue_encode(root) -> list:
    """A minimal devalue encoder, enough to build Nuxt payloads for tests."""
    payload: list = []

    def add(value) -> int:
        index = len(payload)
        payload.append(None)
        if isinstance(value, Wrap):
            payload[index] = [value.tag, add(value.value)]
        elif isinstance(value, dict):
            payload[index] = {k: add(v) for k, v in value.items()}
        elif isinstance(value, (list, tuple)):
            payload[index] = [add(v) for v in value]
        else:
            payload[index] = value
        return index

    add(root)
    return payload


def ad_page_html(advert: dict) -> str:
    """An ad page the way jiji.ug serves it: the advert inside Nuxt's payload."""
    root = Wrap(
        "ShallowReactive",
        {
            "data": Wrap("ShallowReactive", {f"advert-item-{advert['guid']}": {"advert": {"advert": advert}}}),
            "state": {},
            "pinia": {"AdvertsStore": Wrap("Reactive", {"advertItem": advert})},
        },
    )
    payload = json.dumps(devalue_encode(root))
    return f'<html><body><h1>x</h1><script type="application/json" id="__NUXT_DATA__" data-ssr="true">{payload}</script></body></html>'


@pytest.fixture
def advert() -> dict:
    return json.loads((FIXTURES / "advert.json").read_text(encoding="utf-8"))


@pytest.fixture
def listing_page() -> dict:
    return json.loads((FIXTURES / "listing_page.json").read_text(encoding="utf-8"))
