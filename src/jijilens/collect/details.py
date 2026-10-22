"""Phase 2: open each discovered ad and store its details and image list.

The ad page's server-rendered HTML embeds the full advert in its Nuxt data
(see `nuxt.py`), so no browser is needed. Only allowlisted fields are kept
(see `models.py`).
"""

from __future__ import annotations

from typing import Callable
from urllib.parse import urlparse

import httpx
import psycopg

from jijilens.collect import repo
from jijilens.collect.http import NotFound, PoliteClient, TooManyFailures
from jijilens.collect.models import Listing, listing_from_advert
from jijilens.collect.nuxt import NuxtDataError, decode, extract_payload, find_advert
from jijilens.config import CollectConfig


def parse_ad_page(html: str, base_url: str) -> Listing:
    return listing_from_advert(find_advert(decode(extract_payload(html))), base_url)


def fetch_details(
    conn: psycopg.Connection,
    client: PoliteClient,
    config: CollectConfig,
    *,
    limit: int | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    rows = repo.pending_details(conn, limit)
    log(f"ads to read: {len(rows)}")
    counts = {"detailed": 0, "gone": 0, "failed": 0}
    parse_failures_in_a_row = 0
    for n, row in enumerate(rows, 1):
        url = row["source_url"]
        try:
            response = client.get(url, delay=config.http.page_delay_seconds, accept="text/html")
        except NotFound:
            repo.mark_listing(conn, row["id"], "gone")
            conn.commit()
            counts["gone"] += 1
            log(f"  [{n}/{len(rows)}] gone: {url}")
            continue
        except httpx.HTTPError as exc:
            repo.mark_listing(conn, row["id"], "failed", f"{type(exc).__name__}: {exc}")
            conn.commit()
            counts["failed"] += 1
            log(f"  [{n}/{len(rows)}] failed: {url}: {exc}")
            continue

        try:
            listing = parse_ad_page(response.text, config.base_url)
        except (NuxtDataError, KeyError, TypeError, ValueError) as exc:
            if _redirected_away(url, str(response.url)):
                # Closed ads redirect to a category page.
                repo.mark_listing(conn, row["id"], "gone", f"redirected to {response.url}")
                conn.commit()
                counts["gone"] += 1
                log(f"  [{n}/{len(rows)}] gone (redirected): {url}")
                continue
            repo.mark_listing(conn, row["id"], "failed", f"{type(exc).__name__}: {exc}")
            conn.commit()
            counts["failed"] += 1
            parse_failures_in_a_row += 1
            log(f"  [{n}/{len(rows)}] could not read the ad data: {url}: {exc}")
            if parse_failures_in_a_row >= config.http.stop_after_consecutive_failures:
                raise TooManyFailures(
                    f"{parse_failures_in_a_row} ad pages in a row had no readable data; did jiji.ug change its pages?"
                ) from exc
            continue

        parse_failures_in_a_row = 0
        images = listing.images[: config.max_images_per_listing]
        repo.save_listing(conn, row["id"], listing, images)
        conn.commit()
        status = "detailed" if listing.is_active else "gone"
        counts[status] += 1
        log(f"  [{n}/{len(rows)}] {listing.title[:60]!r}: {len(images)} image(s){'' if listing.is_active else ', closed'}")
    return counts


def _redirected_away(requested: str, final: str) -> bool:
    return urlparse(requested).path != urlparse(final).path
