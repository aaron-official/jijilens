"""SQL for embeddings and nearest-neighbour search. Callers commit."""

from __future__ import annotations

from typing import Iterable

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg import sql

# Rows a search may return: active listings we finished collecting.
_LIVE = "l.status = 'detailed' AND l.is_active AND i.status = 'ok'"


def prepare(conn: psycopg.Connection, *, ef_search: int = 100) -> None:
    """Let this connection send and receive numpy vectors, and tune HNSW search.

    ef_search is how many candidates HNSW keeps while walking its graph: higher
    is more accurate and slower. Iterative scan lets a filtered query (say,
    sofas only) keep walking until it has enough rows instead of returning short.
    """
    register_vector(conn)
    conn.execute(sql.SQL("SET hnsw.ef_search = {}").format(sql.Literal(ef_search)))
    conn.execute("SET hnsw.iterative_scan = relaxed_order")


def ensure_model(conn: psycopg.Connection, name: str, dim: int) -> int:
    row = conn.execute(
        """INSERT INTO embedding_models (name, dim) VALUES (%s, %s)
           ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
           RETURNING id, dim""",
        (name, dim),
    ).fetchone()
    if row["dim"] != dim:
        raise ValueError(f"model {name} is stored with dim {row['dim']}, not {dim}")
    return row["id"]


def pending_embeddings(conn: psycopg.Connection, model_id: int, limit: int | None = None) -> list[dict]:
    """Downloaded images this model has not embedded yet."""
    return conn.execute(
        """SELECT i.id, i.local_path
           FROM listing_images i
           WHERE i.status = 'ok'
             AND NOT EXISTS (SELECT 1 FROM image_embeddings e WHERE e.image_id = i.id AND e.model_id = %s)
           ORDER BY i.id
           LIMIT %s""",
        (model_id, limit),
    ).fetchall()


def save_embeddings(conn: psycopg.Connection, model_id: int, rows: Iterable[tuple[int, np.ndarray]]) -> None:
    with conn.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO image_embeddings (image_id, model_id, embedding) VALUES (%s, %s, %s)
               ON CONFLICT (image_id, model_id) DO UPDATE SET embedding = EXCLUDED.embedding, created_at = now()""",
            [(image_id, model_id, vector) for image_id, vector in rows],
        )


def ensure_index(conn: psycopg.Connection, model_id: int, dim: int) -> str:
    """An HNSW index for one model's vectors. Building it after the bulk insert is much faster."""
    name = f"image_embeddings_model{int(model_id)}_hnsw"
    conn.execute(
        sql.SQL(
            "CREATE INDEX IF NOT EXISTS {name} ON image_embeddings"
            " USING hnsw ((embedding::vector({dim})) vector_cosine_ops) WHERE model_id = {model_id}"
        ).format(name=sql.Identifier(name), dim=sql.SQL(str(int(dim))), model_id=sql.SQL(str(int(model_id))))
    )
    return name


def nearest_images(
    conn: psycopg.Connection,
    model_id: int,
    dim: int,
    query: np.ndarray,
    *,
    limit: int,
    category: str | None = None,
    exclude_image_id: int | None = None,
) -> list[dict]:
    """The `limit` catalog images closest to `query`, most similar first.

    model_id and dim are written into the SQL rather than bound as parameters:
    Postgres only uses the per-model partial index when the query states the
    same `model_id = N` and `vector(dim)` cast that the index was built with.
    """
    filters = [sql.SQL(_LIVE)]
    params: dict = {"query": query, "limit": limit}
    if category is not None:
        filters.append(sql.SQL("c.key = %(category)s"))
        params["category"] = category
    if exclude_image_id is not None:
        filters.append(sql.SQL("e.image_id <> %(exclude)s"))
        params["exclude"] = exclude_image_id
    distance = sql.SQL("e.embedding::vector({dim}) <=> %(query)s").format(dim=sql.SQL(str(int(dim))))
    statement = sql.SQL(
        """SELECT e.image_id, i.listing_id, i.local_path, c.key AS category, 1 - ({distance}) AS similarity
           FROM image_embeddings e
           JOIN listing_images i ON i.id = e.image_id
           JOIN listings l ON l.id = i.listing_id
           JOIN categories c ON c.id = l.collection_category_id
           WHERE e.model_id = {model_id} AND {filters}
           ORDER BY {distance}
           LIMIT %(limit)s"""
    ).format(distance=distance, model_id=sql.SQL(str(int(model_id))), filters=sql.SQL(" AND ").join(filters))
    return conn.execute(statement, params).fetchall()


def listing_cards(conn: psycopg.Connection, listing_ids: list[int]) -> dict[int, dict]:
    """What a search result shows for each listing."""
    rows = conn.execute(
        """SELECT l.id, l.title, l.price_ugx, l.price_text, l.region, l.source_url, c.key AS category
           FROM listings l JOIN categories c ON c.id = l.collection_category_id
           WHERE l.id = ANY(%s)""",
        (listing_ids,),
    ).fetchall()
    return {row["id"]: row for row in rows}


def all_embeddings(conn: psycopg.Connection, model_id: int) -> list[dict]:
    """Every live image's vector for one model, for exact (brute-force) evaluation in memory."""
    return conn.execute(
        f"""SELECT e.image_id, i.listing_id, i.position, c.key AS category, e.embedding
            FROM image_embeddings e
            JOIN listing_images i ON i.id = e.image_id
            JOIN listings l ON l.id = i.listing_id
            JOIN categories c ON c.id = l.collection_category_id
            WHERE e.model_id = %s AND {_LIVE}
            ORDER BY e.image_id""",
        (model_id,),
    ).fetchall()


def embedding_counts(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        """SELECT m.name, m.dim, count(e.image_id) AS images
           FROM embedding_models m LEFT JOIN image_embeddings e ON e.model_id = m.id
           GROUP BY m.id ORDER BY m.id"""
    ).fetchall()
