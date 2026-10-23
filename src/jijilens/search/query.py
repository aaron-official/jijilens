"""Search the catalog with a photo, or with one object cropped out of it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import psycopg
from PIL import Image, ImageOps

from jijilens.search import store
from jijilens.search.embedder import Embedder

# Each listing has up to 5 images. Fetch enough nearest images that, after
# keeping one per listing, k distinct listings are usually left.
IMAGES_PER_RESULT = 8


@dataclass(frozen=True)
class Match:
    listing_id: int
    similarity: float  # of the listing's best-matching image
    image_id: int
    local_path: str
    category: str
    image_url: str = ""  # Jiji's own copy of the matched photo


def parse_box(text: str) -> tuple[int, int, int, int]:
    """'x1,y1,x2,y2' in pixels, as an object detector reports a box."""
    try:
        x1, y1, x2, y2 = (int(round(float(part))) for part in text.split(","))
    except ValueError:
        raise ValueError(f"crop must be x1,y1,x2,y2 in pixels, got {text!r}") from None
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"crop {text!r} is empty: x2 must be > x1 and y2 > y1")
    return x1, y1, x2, y2


def load_query_image(path: Path, box: tuple[int, int, int, int] | None = None) -> Image.Image:
    """Phone photos often store rotation in EXIF; apply it so crop coordinates match what you see."""
    with Image.open(path) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
    if box is not None:
        x1, y1, x2, y2 = box
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(image.width, x2), min(image.height, y2)
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"crop {box} is outside the {image.width}x{image.height} image")
        image = image.crop((x1, y1, x2, y2))
    return image


def rank_listings(hits: Iterable[dict], k: int) -> list[Match]:
    """One result per listing, scored by its best-matching image, most similar first.

    Without this, a listing with five near-identical photos would fill half the results.
    """
    best: dict[int, Match] = {}
    for hit in hits:
        similarity = float(hit["similarity"])
        current = best.get(hit["listing_id"])
        if current is None or similarity > current.similarity:
            best[hit["listing_id"]] = Match(
                hit["listing_id"], similarity, hit["image_id"], hit["local_path"], hit["category"], hit.get("image_url") or ""
            )
    return sorted(best.values(), key=lambda m: m.similarity, reverse=True)[:k]


def search(
    conn: psycopg.Connection,
    embedder: Embedder,
    model_id: int,
    image: Image.Image,
    *,
    k: int = 10,
    category: str | None = None,
) -> list[Match]:
    query = embedder.embed([image])[0]
    hits = store.nearest_images(
        conn, model_id, embedder.spec.dim, query, limit=k * IMAGES_PER_RESULT, category=category
    )
    return rank_listings(hits, k)
