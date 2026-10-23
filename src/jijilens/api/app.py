"""HTTP API and web page: upload a photo, find the objects in it, search with one.

    POST /api/photos                  upload -> {photo_id, width, height}
    POST /api/photos/{id}/objects     detected, named boxes (slow on CPU; the page asks in the background)
    POST /api/photos/{id}/search      {box, category, k} -> matching listings
    GET  /api/categories, /api/health

Models are loaded by the caller and passed in, so the app starts fast in
tests and loads them exactly once when served. Inference runs in FastAPI's
thread pool; a lock per model keeps two requests from running the same model
at once and fighting over the CPU.
"""

from __future__ import annotations

import io
import os
import threading
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from jijilens.api.limits import Limits, RateLimiter
from jijilens.search import store
from jijilens.search.detector import LABELS, Detector
from jijilens.search.embedder import Embedder
from jijilens.search.query import search_vector

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_SIDE = 1600  # uploads are scaled down to this; boxes are in the scaled photo's pixels
# Recent uploads kept in memory so each search doesn't re-upload; each is up to ~7.5 MB.
PHOTOS_KEPT = int(os.environ.get("JIJILENS_PHOTOS_KEPT", "20"))


@dataclass
class Photo:
    image: Image.Image
    # Vectors of the detected objects' crops, computed once at detection, so
    # tapping an object only has to query the database.
    vectors: dict[tuple[int, int, int, int], np.ndarray] = field(default_factory=dict)
    objects: list[dict] | None = None  # detection result, so a photo is detected once


class PhotoCache:
    """The last few uploads, in memory. Nothing is written to disk."""

    def __init__(self, size: int = PHOTOS_KEPT):
        self._photos: OrderedDict[str, Photo] = OrderedDict()
        self._size = size
        self._lock = threading.Lock()

    def add(self, image: Image.Image) -> str:
        photo_id = uuid.uuid4().hex
        with self._lock:
            self._photos[photo_id] = Photo(image)
            while len(self._photos) > self._size:
                self._photos.popitem(last=False)
        return photo_id

    def get(self, photo_id: str) -> Photo:
        with self._lock:
            if photo_id not in self._photos:
                raise HTTPException(404, "photo expired; upload it again")
            self._photos.move_to_end(photo_id)
            return self._photos[photo_id]


class SearchRequest(BaseModel):
    box: list[int] | None = Field(default=None, min_length=4, max_length=4, description="x1,y1,x2,y2; none for the whole photo")
    category: str | None = None
    k: int = Field(default=12, ge=1, le=48)


def read_photo(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(415, "not an image we can read") from exc
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    return image


def crop(image: Image.Image, box: list[int] | None) -> Image.Image:
    if box is None:
        return image
    x1, y1 = max(0, box[0]), max(0, box[1])
    x2, y2 = min(image.width, box[2]), min(image.height, box[3])
    if x2 - x1 < 8 or y2 - y1 < 8:
        raise HTTPException(422, "box is too small or outside the photo")
    return image.crop((x1, y1, x2, y2))


def create_app(
    database_url: str, embedder: Embedder, detector: Detector | None = None, limits: Limits | None = None
) -> FastAPI:
    def configure(conn) -> None:
        store.prepare(conn)
        conn.commit()

    pool = ConnectionPool(
        database_url, min_size=1, max_size=4, kwargs={"row_factory": dict_row}, configure=configure, open=False
    )
    photos = PhotoCache()
    limiter = RateLimiter(limits or Limits.from_env())
    embed_lock, detect_lock = threading.Lock(), threading.Lock()
    spec = embedder.spec
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Words in the same space as image crops, to name what the detector found.
        state["label_vectors"] = embedder.embed_texts([f"a photo of a {label}" for label in LABELS])
        pool.open(wait=True)
        with pool.connection() as conn:
            state["model_id"] = store.model_id(conn, spec.name)
        if state["model_id"] is None:
            raise RuntimeError(f"no {spec.name} embeddings in the database; run: jijilens embed --model {spec.name}")
        yield
        pool.close()

    app = FastAPI(title="JijiLens", lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "model": spec.name, "detector": detector.spec.name if detector else None}

    @app.get("/api/categories")
    def categories() -> list[dict]:
        with pool.connection() as conn:
            return store.categories(conn)

    @app.post("/api/photos")
    def upload(request: Request, file: UploadFile = File(...)) -> dict:
        limiter.photo(request)
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"photo is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        image = read_photo(data)
        return {"photo_id": photos.add(image), "width": image.width, "height": image.height}

    @app.post("/api/photos/{photo_id}/objects")
    def objects(photo_id: str) -> dict:
        if detector is None:
            raise HTTPException(503, "object detection is off; draw a box instead")
        photo = photos.get(photo_id)
        if photo.objects is not None:
            return {"objects": photo.objects}
        with detect_lock:
            found = detector.detect(photo.image)
        if not found:
            photo.objects = []
            return {"objects": []}
        # The detector's boxes are good but its names often aren't; SigLIP names each crop instead.
        with embed_lock:
            vectors = embedder.embed([photo.image.crop(d.box) for d in found])
        names = np.argmax(vectors @ state["label_vectors"].T, axis=1)
        objects = []
        for d, vector, name in zip(found, vectors, names):
            photo.vectors[d.box] = vector
            objects.append({"box": list(d.box), "label": LABELS[name], "detector_label": d.label, "score": round(d.score, 3)})
        photo.objects = objects
        return {"objects": objects}

    @app.post("/api/photos/{photo_id}/search")
    def search_photo(photo_id: str, body: SearchRequest, request: Request) -> dict:
        limiter.search(request)
        photo = photos.get(photo_id)
        vector = photo.vectors.get(tuple(body.box)) if body.box else None
        if vector is None:
            query = crop(photo.image, body.box)
            with embed_lock:
                vector = embedder.embed([query])[0]
        with pool.connection() as conn:
            matches = search_vector(conn, state["model_id"], spec.dim, vector, k=body.k, category=body.category)
            cards = store.listing_cards(conn, [m.listing_id for m in matches])
        results = [
            {
                "listing_id": m.listing_id,
                "similarity": round(m.similarity, 4),
                "category": m.category,
                "image_url": m.image_url,
                "title": cards[m.listing_id]["title"],
                "price_ugx": cards[m.listing_id]["price_ugx"],
                "price_text": cards[m.listing_id]["price_text"],
                "region": cards[m.listing_id]["region"],
                "url": cards[m.listing_id]["source_url"],
            }
            for m in matches
            if m.listing_id in cards
        ]
        threshold = spec.close_match
        close = bool(results) and (threshold is None or results[0]["similarity"] >= threshold)
        return {"results": results, "close_match": close, "threshold": threshold}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
