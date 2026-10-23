"""Embed every downloaded catalog image with one model, then index the vectors.

Works through images the model has not embedded yet and commits each batch,
so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import psycopg
from PIL import Image, UnidentifiedImageError

from jijilens.search import store
from jijilens.search.embedder import Embedder


def embed_catalog(
    conn: psycopg.Connection,
    embedder: Embedder,
    data_dir: Path,
    *,
    batch_size: int = 16,
    limit: int | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    spec = embedder.spec
    model_id = store.ensure_model(conn, spec.name, spec.dim)
    conn.commit()
    rows = store.pending_embeddings(conn, model_id, limit)
    log(f"images to embed with {spec.name}: {len(rows)}")
    counts = {"embedded": 0, "unreadable": 0}
    started = time.monotonic()
    for start in range(0, len(rows), batch_size):
        ids, images = [], []
        for row in rows[start : start + batch_size]:
            try:
                with Image.open(data_dir / row["local_path"]) as image:
                    images.append(image.convert("RGB"))
                ids.append(row["id"])
            except (OSError, UnidentifiedImageError) as exc:
                counts["unreadable"] += 1
                log(f"  unreadable, skipped: {row['local_path']}: {exc}")
        if images:
            store.save_embeddings(conn, model_id, zip(ids, embedder.embed(images)))
            conn.commit()
            counts["embedded"] += len(images)
        done = min(start + batch_size, len(rows))
        rate = counts["embedded"] / max(time.monotonic() - started, 1e-9)
        left = (len(rows) - done) / rate / 60 if rate else 0
        log(f"  [{done}/{len(rows)}] {rate:.1f} images/s, about {left:.0f} min left")

    index = store.ensure_index(conn, model_id, spec.dim)
    conn.commit()
    log(f"index ready: {index}")
    return counts
