"""SQL for the collection phases. Callers commit."""

from __future__ import annotations

from typing import Iterable

import psycopg
from psycopg.types.json import Jsonb

from jijilens.collect.models import ImageRef, Listing, ListingStub
from jijilens.config import Category


def ensure_categories(conn: psycopg.Connection, categories: Iterable[Category]) -> dict[str, int]:
    """Insert or update the configured categories. Returns key -> id."""
    ids: dict[str, int] = {}
    for category in categories:
        row = conn.execute(
            """INSERT INTO categories (key, label, jiji_slug, filters) VALUES (%s, %s, %s, %s)
               ON CONFLICT (key) DO UPDATE
               SET label = EXCLUDED.label, jiji_slug = EXCLUDED.jiji_slug, filters = EXCLUDED.filters
               RETURNING id""",
            (category.key, category.label, category.jiji_slug, Jsonb(category.filters)),
        ).fetchone()
        ids[category.key] = row["id"]
    return ids


def count_listings(conn: psycopg.Connection, category_id: int) -> int:
    row = conn.execute(
        "SELECT count(*) AS n FROM listings WHERE collection_category_id = %s", (category_id,)
    ).fetchone()
    return row["n"]


def add_stubs(conn: psycopg.Connection, category_id: int, stubs: Iterable[ListingStub]) -> int:
    """Save newly discovered ads. Ads already known (from any category) are left alone."""
    added = 0
    for stub in stubs:
        cursor = conn.execute(
            """INSERT INTO listings (source_id, jiji_id, collection_category_id, jiji_category_slug, title, source_url)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (source_id) DO NOTHING""",
            (stub.source_id, stub.jiji_id, category_id, stub.jiji_category_slug, stub.title, stub.source_url),
        )
        added += cursor.rowcount
    return added


def pending_details(conn: psycopg.Connection, limit: int | None = None) -> list[dict]:
    return conn.execute(
        """SELECT l.id, l.source_id, l.source_url
           FROM listings l
           WHERE l.status = 'discovered'
           ORDER BY l.id
           LIMIT %s""",
        (limit,),
    ).fetchall()


def save_listing(conn: psycopg.Connection, listing_id: int, listing: Listing, images: list[ImageRef]) -> None:
    """Store an ad's details and queue its images. Gone ads get no images."""
    status = "detailed" if listing.is_active else "gone"
    conn.execute(
        """UPDATE listings SET
             jiji_id = COALESCE(%(jiji_id)s, jiji_id),
             jiji_category_slug = %(jiji_category_slug)s,
             jiji_category_name = %(jiji_category_name)s,
             title = %(title)s,
             description = %(description)s,
             attributes = %(attributes)s,
             price_ugx = %(price_ugx)s,
             price_text = %(price_text)s,
             region = %(region)s,
             region_slug = %(region_slug)s,
             source_url = COALESCE(NULLIF(%(source_url)s, ''), source_url),
             is_active = %(is_active)s,
             posted_at = %(posted_at)s,
             edited_at = %(edited_at)s,
             status = %(status)s,
             error = NULL,
             updated_at = now()
           WHERE id = %(id)s""",
        {
            "id": listing_id,
            "jiji_id": listing.jiji_id,
            "jiji_category_slug": listing.jiji_category_slug,
            "jiji_category_name": listing.jiji_category_name,
            "title": listing.title,
            "description": listing.description,
            "attributes": Jsonb(listing.attributes),
            "price_ugx": listing.price_ugx,
            "price_text": listing.price_text,
            "region": listing.region,
            "region_slug": listing.region_slug,
            "source_url": listing.source_url,
            "is_active": listing.is_active,
            "posted_at": listing.posted_at,
            "edited_at": listing.edited_at,
            "status": status,
        },
    )
    if status != "detailed":
        return
    for image in images:
        conn.execute(
            """INSERT INTO listing_images (listing_id, source_image_id, position, is_main, url, width, height)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (listing_id, source_image_id) DO NOTHING""",
            (listing_id, image.source_image_id, image.position, image.is_main, image.url, image.width, image.height),
        )


def mark_listing(conn: psycopg.Connection, listing_id: int, status: str, error: str | None = None) -> None:
    conn.execute(
        "UPDATE listings SET status = %s, error = %s, updated_at = now() WHERE id = %s",
        (status, error[:500] if error else None, listing_id),
    )


def pending_images(conn: psycopg.Connection, limit: int | None = None) -> list[dict]:
    return conn.execute(
        """SELECT i.id, i.source_image_id, i.url, l.source_id, c.key AS category_key
           FROM listing_images i
           JOIN listings l ON l.id = i.listing_id
           JOIN categories c ON c.id = l.collection_category_id
           WHERE i.status = 'pending'
           ORDER BY i.listing_id, i.position
           LIMIT %s""",
        (limit,),
    ).fetchall()


def sha256_known(conn: psycopg.Connection, sha256: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM listing_images WHERE sha256 = %s AND status = 'ok' LIMIT 1", (sha256,)
    ).fetchone()
    return row is not None


def save_image(
    conn: psycopg.Connection,
    image_id: int,
    *,
    status: str,
    local_path: str | None = None,
    width: int | None = None,
    height: int | None = None,
    sha256: str | None = None,
    phash: str | None = None,
    size: int | None = None,
    reason: str | None = None,
) -> None:
    conn.execute(
        """UPDATE listing_images SET
             status = %s, local_path = %s,
             width = COALESCE(%s, width), height = COALESCE(%s, height),
             sha256 = %s, phash = %s, bytes = %s, reject_reason = %s, updated_at = now()
           WHERE id = %s""",
        (status, local_path, width, height, sha256, phash, size, reason[:500] if reason else None, image_id),
    )


def stats(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        """SELECT c.key, c.label,
             count(DISTINCT l.id) AS listings,
             count(DISTINCT l.id) FILTER (WHERE l.status = 'discovered') AS discovered,
             count(DISTINCT l.id) FILTER (WHERE l.status = 'detailed') AS detailed,
             count(DISTINCT l.id) FILTER (WHERE l.status = 'gone') AS gone,
             count(DISTINCT l.id) FILTER (WHERE l.status = 'failed') AS failed,
             count(i.id) FILTER (WHERE i.status = 'pending') AS images_pending,
             count(i.id) FILTER (WHERE i.status = 'ok') AS images_ok,
             count(i.id) FILTER (WHERE i.status = 'rejected') AS images_rejected,
             count(i.id) FILTER (WHERE i.status = 'failed') AS images_failed,
             COALESCE(sum(i.bytes) FILTER (WHERE i.status = 'ok'), 0) AS image_bytes
           FROM categories c
           LEFT JOIN listings l ON l.collection_category_id = c.id
           LEFT JOIN listing_images i ON i.listing_id = l.id
           GROUP BY c.id
           ORDER BY c.id"""
    ).fetchall()


def export_listings(conn: psycopg.Connection) -> Iterable[dict]:
    """Detailed, active listings that have at least one downloaded image."""
    return conn.execute(
        """SELECT l.source_id, c.key AS category, l.jiji_category_slug, l.jiji_category_name,
                  l.title, l.description, l.attributes, l.price_ugx, l.price_text,
                  l.region, l.region_slug, l.source_url, l.posted_at, l.edited_at,
                  (SELECT i.url FROM listing_images i
                   WHERE i.listing_id = l.id AND i.status = 'ok'
                   ORDER BY i.position LIMIT 1) AS main_image_url
           FROM listings l
           JOIN categories c ON c.id = l.collection_category_id
           WHERE l.status = 'detailed' AND l.is_active
             AND EXISTS (SELECT 1 FROM listing_images i WHERE i.listing_id = l.id AND i.status = 'ok')
           ORDER BY l.id"""
    )


def export_images(conn: psycopg.Connection) -> Iterable[dict]:
    return conn.execute(
        """SELECT i.source_image_id AS image_id, l.source_id AS listing_id, c.key AS category,
                  i.position, i.is_main, i.url, i.local_path, i.width, i.height, i.sha256, i.phash, i.bytes
           FROM listing_images i
           JOIN listings l ON l.id = i.listing_id
           JOIN categories c ON c.id = l.collection_category_id
           WHERE i.status = 'ok' AND l.status = 'detailed' AND l.is_active
           ORDER BY l.id, i.position"""
    )
