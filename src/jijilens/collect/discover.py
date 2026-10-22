"""Phase 1: page through each category's listing API and save the ads found.

jiji.ug's own front end loads category pages from
`/api_web/v1/listing?slug=<category>&<filters>&page=N`, which answers JSON with 24 ads
a page and the URL of the next page. Reading that instead of scrolling a
browser is faster, and much lighter on the site.
"""

from __future__ import annotations

from typing import Callable
from urllib.parse import urlencode

import httpx
import psycopg

from jijilens.collect import repo
from jijilens.collect.http import PoliteClient
from jijilens.collect.models import stub_from_list_item
from jijilens.config import CollectConfig


def listing_api_url(base_url: str, slug: str, filters: dict[str, str] | None = None) -> str:
    params = {"slug": slug, "init_page": "true", "webp": "true", **(filters or {})}
    return f"{base_url}/api_web/v1/listing?" + urlencode(params)


def discover(
    conn: psycopg.Connection,
    client: PoliteClient,
    config: CollectConfig,
    *,
    log: Callable[[str], None] = print,
) -> int:
    """Fill each category up to `max_listings_per_category`. Returns how many ads were added."""
    category_ids = repo.ensure_categories(conn, config.categories)
    conn.commit()
    total = 0
    for category in config.categories:
        category_id = category_ids[category.key]
        have = repo.count_listings(conn, category_id)
        cap = config.max_listings_per_category
        if have >= cap:
            log(f"{category.key}: {have} listings, already at the cap of {cap}")
            continue
        log(f"{category.key}: {have} listings, collecting up to {cap}")
        url: str | None = listing_api_url(config.base_url, category.jiji_slug, category.filters)
        pages = 0
        while url and have < cap and pages < config.max_pages_per_category:
            try:
                data = client.get(url, delay=config.http.page_delay_seconds, accept="application/json").json()
            except (httpx.HTTPError, ValueError) as exc:
                # Keep what this category has so far; the next run tops it up.
                log(f"  page {pages + 1} failed, moving on to the next category: {exc}")
                break
            pages += 1
            adverts = (data.get("adverts_list") or {}).get("adverts") or []
            stubs = [s for s in (stub_from_list_item(item, config.base_url) for item in adverts) if s]
            added = repo.add_stubs(conn, category_id, stubs[: cap - have])
            conn.commit()
            have += added
            total += added
            log(f"  page {pages}: {len(adverts)} ads, {added} new, {have}/{cap}")
            if not adverts:
                break
            url = data.get("next_url")
    return total
