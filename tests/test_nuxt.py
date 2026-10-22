import json
import math

import pytest

from jijilens.collect.nuxt import NuxtDataError, decode, extract_payload, find_advert
from tests.conftest import Wrap, ad_page_html, devalue_encode


def test_decode_roundtrip_unwraps_vue_reducers():
    value = {"a": Wrap("Reactive", {"b": [1, "two", None, True]}), "c": Wrap("Ref", 3.5)}
    assert decode(devalue_encode(value)) == {"a": {"b": [1, "two", None, True]}, "c": 3.5}


def test_decode_shared_references_and_typed_values():
    # index 1 is referenced twice; Set, Date and special indices
    payload = [{"x": 1, "y": 1, "s": 2, "d": 4, "u": -1, "inf": -4}, {"k": 5}, ["Set", 3, 5], "item", ["Date", "2026-10-22T00:00:00Z"], "v"]
    root = decode(payload)
    assert root["x"] is root["y"]
    assert root["x"] == {"k": "v"}
    assert root["s"] == ["item", "v"]
    assert root["d"] == "2026-10-22T00:00:00Z"
    assert root["u"] is None
    assert math.isinf(root["inf"])


def test_decode_cycle_does_not_recurse_forever():
    payload = [{"self": 0}]
    root = decode(payload)
    assert root["self"] is root


def test_extract_payload_and_find_advert(advert):
    root = decode(extract_payload(ad_page_html(advert)))
    assert find_advert(root)["guid"] == advert["guid"]


def test_find_advert_falls_back_to_fetch_data(advert):
    root = {"data": {f"advert-item-{advert['guid']}": {"advert": {"advert": advert}}}, "pinia": {}}
    assert find_advert(root)["title"] == advert["title"]


def test_missing_payload_raises():
    with pytest.raises(NuxtDataError):
        extract_payload("<html>no data</html>")
    with pytest.raises(NuxtDataError):
        find_advert(decode(json.loads('[{"data": 1}, {}]')))
