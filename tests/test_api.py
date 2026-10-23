"""The web API with stand-in models, against the compose database's real catalog.
Skipped when the database is down or has no SigLIP 2 embeddings yet."""

import io

import numpy as np
import psycopg
import pytest
from PIL import Image

from jijilens import db
from jijilens.config import load_settings
from jijilens.search import store
from jijilens.search.detector import Detection, DetectorSpec, clean, iou
from jijilens.search.embedder import get_spec

SPEC = get_spec("siglip2-b16")


class FakeEmbedder:
    spec = SPEC

    def __init__(self):
        self.calls = []

    def embed(self, images):
        self.calls.append([image.size for image in images])
        rng = np.random.default_rng(0)
        vectors = rng.normal(size=(len(images), SPEC.dim)).astype(np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


class FakeDetector:
    spec = DetectorSpec("fake", "fake", "omdet", 0.3)

    def detect(self, image):
        return [Detection((10, 20, 110, 220), "speaker", 0.8)]


@pytest.fixture(scope="module")
def database_url():
    url = load_settings().database_url
    try:
        with db.connect(url) as conn:
            if store.model_id(conn, SPEC.name) is None:
                pytest.skip("no siglip2-b16 embeddings; run jijilens embed")
    except psycopg.OperationalError as exc:
        pytest.skip(f"database not reachable: {exc}")
    return url


def make_client(database_url, detector=None, embedder=None):
    from fastapi.testclient import TestClient

    from jijilens.api.app import create_app

    return TestClient(create_app(database_url, embedder or FakeEmbedder(), detector))


def jpeg(width=400, height=300):
    out = io.BytesIO()
    Image.new("RGB", (width, height), "orange").save(out, format="JPEG")
    return out.getvalue()


def test_upload_detect_and_search(database_url):
    embedder = FakeEmbedder()
    with make_client(database_url, FakeDetector(), embedder) as client:
        assert client.get("/api/health").json()["detector"] == "fake"
        assert any(c["key"] == "sofas" for c in client.get("/api/categories").json())

        photo = client.post("/api/photos", files={"file": ("room.jpg", jpeg(), "image/jpeg")}).json()
        assert (photo["width"], photo["height"]) == (400, 300)

        objects = client.post(f"/api/photos/{photo['photo_id']}/objects").json()["objects"]
        assert objects == [{"box": [10, 20, 110, 220], "label": "speaker", "score": 0.8}]

        found = client.post(f"/api/photos/{photo['photo_id']}/search", json={"box": [10, 20, 110, 220], "k": 5}).json()
        assert embedder.calls[-1] == [(100, 200)]  # searched with the crop, not the whole photo
        assert 1 <= len(found["results"]) <= 5
        first = found["results"][0]
        assert first["url"].startswith("https://jiji.ug/") and first["image_url"].startswith("http")
        assert found["threshold"] == SPEC.close_match

        sofas = client.post(f"/api/photos/{photo['photo_id']}/search", json={"category": "sofas", "k": 3}).json()
        assert {r["category"] for r in sofas["results"]} == {"sofas"}


def test_large_uploads_are_scaled_down(database_url):
    with make_client(database_url) as client:
        photo = client.post("/api/photos", files={"file": ("big.jpg", jpeg(3200, 2400), "image/jpeg")}).json()
        assert (photo["width"], photo["height"]) == (1600, 1200)


def test_errors(database_url):
    with make_client(database_url) as client:
        assert client.post("/api/photos", files={"file": ("x.jpg", b"not an image", "image/jpeg")}).status_code == 415
        assert client.post("/api/photos/nope/search", json={}).status_code == 404
        photo = client.post("/api/photos", files={"file": ("room.jpg", jpeg(), "image/jpeg")}).json()
        assert client.post(f"/api/photos/{photo['photo_id']}/objects").status_code == 503  # no detector
        assert client.post(f"/api/photos/{photo['photo_id']}/search", json={"box": [500, 500, 600, 600]}).status_code == 422
        assert client.get("/").status_code == 200


def test_clean_drops_duplicates_and_tiny_boxes():
    found = [
        Detection((100, 100, 300, 400), "mobile phone", 0.56),
        Detection((102, 98, 298, 402), "speaker", 0.38),  # same object, second name
        Detection((0, 0, 10, 10), "cup", 0.9),  # too small
        Detection((-5, 500, 200, 900), "shoe", 0.4),  # clamped to the photo
    ]
    kept = clean(found, 960, 800)
    assert [(d.label, d.box) for d in kept] == [("mobile phone", (100, 100, 300, 400)), ("shoe", (0, 500, 200, 800))]
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
