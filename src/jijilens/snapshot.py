"""A slim copy of the searchable catalog, for loading into a fresh database.

The deployed app runs its own Postgres inside the container and loads this at
startup, so it needs no outside database. A snapshot holds only what the demo
shows: categories, active listings (title, price, region, link), their
downloaded images' Jiji URLs, and the image vectors. No descriptions,
attributes or photo files.

On disk it is one gzipped Postgres COPY file per table plus a manifest:

    snapshot/
      manifest.json
      categories.tsv.gz  listings.tsv.gz  listing_images.tsv.gz
      embedding_models.tsv.gz  image_embeddings.tsv.gz
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import psycopg
from psycopg import sql

from jijilens.search import store

_LIVE_LISTING = """l.status = 'detailed' AND l.is_active
    AND EXISTS (SELECT 1 FROM listing_images i WHERE i.listing_id = l.id AND i.status = 'ok')"""

# (table, columns, query selecting those columns). Loaded in this order, so
# rows exist before the rows that reference them.
TABLES = [
    (
        "categories",
        ["id", "key", "label", "jiji_slug", "filters"],
        "SELECT id, key, label, jiji_slug, filters FROM categories ORDER BY id",
    ),
    (
        "listings",
        [
            "id", "source_id", "jiji_id", "collection_category_id", "jiji_category_slug", "jiji_category_name",
            "title", "price_ugx", "price_text", "region", "region_slug", "source_url", "is_active", "status",
            "posted_at", "edited_at",
        ],
        f"""SELECT l.id, l.source_id, l.jiji_id, l.collection_category_id, l.jiji_category_slug, l.jiji_category_name,
                   l.title, l.price_ugx, l.price_text, l.region, l.region_slug, l.source_url, l.is_active, l.status,
                   l.posted_at, l.edited_at
            FROM listings l WHERE {_LIVE_LISTING} ORDER BY l.id""",
    ),
    (
        "listing_images",
        ["id", "listing_id", "source_image_id", "position", "is_main", "url", "width", "height", "status"],
        f"""SELECT i.id, i.listing_id, i.source_image_id, i.position, i.is_main, i.url, i.width, i.height, i.status
            FROM listing_images i JOIN listings l ON l.id = i.listing_id
            WHERE i.status = 'ok' AND {_LIVE_LISTING} ORDER BY i.id""",
    ),
    ("embedding_models", ["id", "name", "dim"], "SELECT id, name, dim FROM embedding_models ORDER BY id"),
    (
        "image_embeddings",
        ["image_id", "model_id", "embedding"],
        f"""SELECT e.image_id, e.model_id, e.embedding
            FROM image_embeddings e
            JOIN listing_images i ON i.id = e.image_id
            JOIN listings l ON l.id = i.listing_id
            WHERE i.status = 'ok' AND {_LIVE_LISTING} ORDER BY e.image_id, e.model_id""",
    ),
]


def export(conn: psycopg.Connection, out_dir: Path, *, log: Callable[[str], None] = print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for table, _columns, query in TABLES:
        path = out_dir / f"{table}.tsv.gz"
        rows = 0
        with conn.cursor() as cursor, gzip.open(path, "wb") as f:
            with cursor.copy(sql.SQL("COPY ({}) TO STDOUT").format(sql.SQL(query))) as copy:
                for chunk in copy:
                    f.write(chunk)
                    rows += bytes(chunk).count(b"\n")
        counts[table] = rows
        log(f"  {table}: {rows} rows, {path.stat().st_size / 1e6:.1f} MB")
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": counts}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def is_empty(conn: psycopg.Connection) -> bool:
    return conn.execute("SELECT NOT EXISTS (SELECT 1 FROM listings) AS empty").fetchone()["empty"]


def load(conn: psycopg.Connection, in_dir: Path, *, log: Callable[[str], None] = print) -> dict:
    """Load a snapshot into a migrated, empty database, then build the vector indexes."""
    manifest = json.loads((in_dir / "manifest.json").read_text(encoding="utf-8"))
    if not is_empty(conn):
        raise RuntimeError("the database already has listings; load a snapshot into an empty one")
    with conn.transaction():
        for table, columns, _query in TABLES:
            statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
                sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns))
            )
            with conn.cursor() as cursor, gzip.open(in_dir / f"{table}.tsv.gz", "rb") as f:
                with cursor.copy(statement) as copy:
                    while chunk := f.read(1 << 20):
                        copy.write(chunk)
            log(f"  {table}: {manifest['rows'].get(table, '?')} rows")
        # Rows came with their ids, so move each id sequence past them.
        for table in ("categories", "listings", "listing_images", "embedding_models"):
            conn.execute(
                sql.SQL("SELECT setval(pg_get_serial_sequence({t}, 'id'), COALESCE((SELECT max(id) FROM {i}), 0) + 1, false)").format(
                    t=sql.Literal(table), i=sql.Identifier(table)
                )
            )
        for model in conn.execute("SELECT id, name, dim FROM embedding_models").fetchall():
            log(f"  index for {model['name']}: {store.ensure_index(conn, model['id'], model['dim'])}")
    conn.execute("ANALYZE")
    conn.commit()
    return manifest
