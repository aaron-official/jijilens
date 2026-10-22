"""Decoding the page data Nuxt embeds in every jiji.ug page.

`<script id="__NUXT_DATA__">` holds a "devalue" payload: one flat JSON array
where index 0 is the root and every array element or object value is an
index into the same array. That lets shared and cyclic values appear once.
Arrays that start with a string are typed values ("Set", "Date", ...) or
Nuxt's Vue wrappers ("Reactive", "Ref", ...), which we unwrap.
"""

from __future__ import annotations

import json
import re
from typing import Any

_SCRIPT = re.compile(r"<script[^>]*\bid=[\"']__NUXT_DATA__[\"'][^>]*>(.*?)</script>", re.S)

# devalue's special indices
_UNDEFINED, _HOLE, _NAN, _POS_INF, _NEG_INF, _NEG_ZERO = -1, -2, -3, -4, -5, -6
_SPECIAL = {
    _UNDEFINED: None,
    _HOLE: None,
    _NAN: float("nan"),
    _POS_INF: float("inf"),
    _NEG_INF: float("-inf"),
    _NEG_ZERO: -0.0,
}


class NuxtDataError(ValueError):
    """The page has no Nuxt data, or not the shape we expect."""


def extract_payload(html: str) -> list:
    match = _SCRIPT.search(html)
    if not match:
        raise NuxtDataError("no __NUXT_DATA__ script on the page")
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise NuxtDataError(f"__NUXT_DATA__ is not JSON: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise NuxtDataError("__NUXT_DATA__ is not a devalue array")
    return payload


def decode(payload: list) -> Any:
    """Rebuild the original value from a devalue payload."""
    memo: dict[int, Any] = {}

    def hydrate(index: int) -> Any:
        if index in _SPECIAL:
            return _SPECIAL[index]
        if index in memo:
            return memo[index]
        value = payload[index]
        if isinstance(value, dict):
            out: dict = {}
            memo[index] = out
            for key, ref in value.items():
                out[key] = hydrate(ref)
            return out
        if isinstance(value, list):
            if value and isinstance(value[0], str):
                result = _typed(value, hydrate)
                memo[index] = result
                return result
            items: list = []
            memo[index] = items
            items.extend(hydrate(ref) for ref in value)
            return items
        memo[index] = value
        return value

    return hydrate(0)


def _typed(value: list, hydrate) -> Any:
    tag = value[0]
    if tag == "Date":
        return value[1]
    if tag == "Set":
        return [hydrate(ref) for ref in value[1:]]
    if tag == "Map":
        return {_key(hydrate(k)): hydrate(v) for k, v in zip(value[1::2], value[2::2])}
    if tag == "null":  # an object without a prototype: literal keys, indexed values
        return {k: hydrate(v) for k, v in zip(value[1::2], value[2::2])}
    if tag == "BigInt":
        return int(value[1])
    if tag in ("RegExp", "Object"):
        return value[1]
    # Nuxt reducers (Reactive, ShallowReactive, Ref, ShallowRef, EmptyRef, ...)
    # and anything newer: the wrapped value is at index 1.
    return hydrate(value[1]) if len(value) > 1 else None


def _key(key: Any) -> Any:
    return key if isinstance(key, (str, int, float, bool, type(None))) else json.dumps(key, default=str)


def find_advert(root: Any) -> dict:
    """The ad on an ad page: the Pinia store's `advertItem`, or the
    `advert-item-<guid>` fetch result when the store is missing."""
    store = _get(root, "pinia", "AdvertsStore", "advertItem")
    if _is_advert(store):
        return store
    data = _get(root, "data")
    if isinstance(data, dict):
        for key, value in data.items():
            if key.startswith("advert-item-"):
                advert = _get(value, "advert", "advert")
                if _is_advert(advert):
                    return advert
    raise NuxtDataError("no advert in the page data (not an ad page, or the site changed)")


def _get(obj: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _is_advert(obj: Any) -> bool:
    return isinstance(obj, dict) and bool(obj.get("guid")) and "title" in obj and "images" in obj
