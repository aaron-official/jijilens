"""What we keep from Jiji's data. This module is the allowlist.

Jiji's advert objects also carry the seller's name, avatar, user id, rating,
store addresses, chat links and tracking data. None of it is read here, so
none of it can reach the database. Free text goes through `privacy.scrub`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

from jijilens.collect.privacy import scrub

ATTRIBUTE_VALUE_CHARS = 200


@dataclass(frozen=True)
class ListingStub:
    """One ad as the category listing shows it: enough to visit it later."""

    source_id: str
    jiji_id: int | None
    title: str
    source_url: str
    jiji_category_slug: str | None


@dataclass(frozen=True)
class ImageRef:
    source_image_id: int
    url: str
    width: int | None
    height: int | None
    is_main: bool
    position: int


@dataclass
class Listing:
    source_id: str
    jiji_id: int | None
    title: str
    description: str | None
    attributes: dict[str, str]
    jiji_category_slug: str | None
    jiji_category_name: str | None
    price_ugx: int | None
    price_text: str | None
    region: str | None
    region_slug: str | None
    source_url: str
    is_active: bool
    posted_at: datetime | None
    edited_at: datetime | None
    images: list[ImageRef] = field(default_factory=list)


def canonical_url(href: str, base_url: str) -> str:
    """Absolute URL without query string or fragment, so tracking params don't create duplicates."""
    parsed = urlparse(urljoin(base_url + "/", href))
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def stub_from_list_item(item: dict, base_url: str) -> ListingStub | None:
    """An entry of the listing API's `adverts_list.adverts`. None for jobs and CVs."""
    if item.get("is_job") or item.get("is_cv"):
        return None
    guid, url, title = item.get("guid"), item.get("url"), item.get("title")
    if not (guid and url and title):
        return None
    return ListingStub(
        source_id=str(guid),
        jiji_id=_int(item.get("id")),
        title=scrub(str(title)),
        source_url=canonical_url(url, base_url),
        jiji_category_slug=item.get("category_slug"),
    )


def listing_from_advert(advert: dict, base_url: str) -> Listing:
    """The `advertItem` of an ad page's Nuxt data."""
    price = advert.get("price") or {}
    price_obj = advert.get("price_obj") or {}
    return Listing(
        source_id=str(advert["guid"]),
        jiji_id=_int(advert.get("id")),
        title=scrub(str(advert.get("title") or "").strip()),
        description=scrub((advert.get("description") or "").strip()) or None,
        attributes=_attributes(advert.get("attrs")),
        jiji_category_slug=advert.get("category_slug"),
        jiji_category_name=advert.get("category_name"),
        price_ugx=_int(price.get("value") if price.get("value") is not None else price_obj.get("value")),
        price_text=price_obj.get("view") or price.get("title"),
        region=advert.get("region_name"),
        region_slug=advert.get("region_slug"),
        source_url=canonical_url(advert["url"], base_url) if advert.get("url") else "",
        is_active=bool(advert.get("is_active", True)) and not advert.get("is_closed"),
        posted_at=_date(advert.get("date_created")),
        edited_at=_date(advert.get("date_edited")),
        images=_images(advert.get("images")),
    )


def _attributes(attrs) -> dict[str, str]:
    """[{"name": "Brand", "value": "Samsung", "unit": null}, ...] -> {"Brand": "Samsung"}."""
    out: dict[str, str] = {}
    for attr in attrs or []:
        if not isinstance(attr, dict):
            continue
        name, value = attr.get("name"), attr.get("value")
        if not name or value in (None, ""):
            continue
        if isinstance(value, list):  # multi-value attributes such as "Features"
            value = ", ".join(str(v) for v in value if v not in (None, ""))
        text = str(value) if not attr.get("unit") else f"{value} {attr['unit']}"
        out[str(name)] = (scrub(text) or "")[:ATTRIBUTE_VALUE_CHARS]
    return out


def _images(images) -> list[ImageRef]:
    """Product photos, main image first, in the seller's order otherwise."""
    usable = [
        img
        for img in images or []
        if isinstance(img, dict) and img.get("url") and img.get("id") is not None and "/no-image/" not in img["url"]
    ]
    usable.sort(key=lambda img: not img.get("is_main"))  # stable: keeps the seller's order
    return [
        ImageRef(
            source_image_id=int(img["id"]),
            url=img["url"],
            width=_int(img.get("width")),
            height=_int(img.get("height")),
            is_main=bool(img.get("is_main")),
            position=position,
        )
        for position, img in enumerate(usable)
    ]


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date(value) -> datetime | None:
    """'Tue, 24 Feb 2026 11:02:26 GMT' -> aware datetime."""
    if not value or not isinstance(value, str):
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
