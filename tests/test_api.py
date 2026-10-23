"""The web API with stand-in models, against a small fake catalog in a throwaway
database, so it runs anywhere a Postgres with pgvector does (locally and in CI)."""

import io

import numpy as np
import pytest
from PIL import Image

from jijilens import db
from jijilens.api.limits import Limits
from jijilens.collect import repo
from jijilens.config import Category
from jijilens.search import store
from jijilens.search.detector import LABELS, Detection, DetectorSpec, clean, iou
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

    def embed_texts(self, texts):
        vectors = np.random.default_rng(1).normal(size=(len(texts), SPEC.dim)).astype(np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


class FakeDetector:
    spec = DetectorSpec("fake", "fake", "omdet", 0.3)

    def detect(self, image):
        return [Detection((10, 20, 110, 220), "mobile phone", 0.8)]


@pytest.fixture(scope="module")
def database_url(new_database):
    """Twelve listings, one photo each, alternating sofas and speakers, with random vectors."""
    url = new_database()
    rng = np.random.default_rng(2)
    with db.connect(url) as conn:
        store.prepare(conn)
        ids = repo.ensure_categories(
            conn, [Category("sofas", "Sofas", "furniture"), Category("speakers", "Speakers", "audio-and-music-equipment")]
        )
        model_id = store.ensure_model(conn, SPEC.name, SPEC.dim)
        vectors = []
        for n in range(12):
            key = "sofas" if n % 2 == 0 else "speakers"
            listing_id = conn.execute(
                """INSERT INTO listings (source_id, collection_category_id, title, source_url, status, price_ugx)
                   VALUES (%s, %s, %s, %s, 'detailed', %s) RETURNING id""",
                (f"T{n}", ids[key], f"{key} {n}", f"https://jiji.ug/x/{n}.html", 100_000 + n),
            ).fetchone()["id"]
            image_id = conn.execute(
                """INSERT INTO listing_images (listing_id, source_image_id, position, url, status)
                   VALUES (%s, %s, 0, %s, 'ok') RETURNING id""",
                (listing_id, n, f"https://pictures-uganda.jijistatic.com/{n}.webp"),
            ).fetchone()["id"]
            vector = rng.normal(size=SPEC.dim).astype(np.float32)
            vectors.append((image_id, vector / np.linalg.norm(vector)))
        store.save_embeddings(conn, model_id, vectors)
        store.ensure_index(conn, model_id, SPEC.dim)
        conn.commit()
    return url


NO_LIMITS = Limits(photos_per_minute=0, searches_per_minute=0, photos_per_day=0)


def make_client(database_url, detector=None, embedder=None, limits=NO_LIMITS):
    from fastapi.testclient import TestClient

    from jijilens.api.app import create_app

    return TestClient(create_app(database_url, embedder or FakeEmbedder(), detector, limits))


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
        assert [(o["box"], o["detector_label"]) for o in objects] == [([10, 20, 110, 220], "mobile phone")]
        assert objects[0]["label"] in LABELS
        assert embedder.calls[-1] == [(100, 200)]  # the crop was embedded once, at detection

        calls = len(embedder.calls)
        found = client.post(f"/api/photos/{photo['photo_id']}/search", json={"box": [10, 20, 110, 220], "k": 5}).json()
        assert len(embedder.calls) == calls  # tapping a detected object reuses its vector

        client.post(f"/api/photos/{photo['photo_id']}/search", json={"box": [0, 0, 50, 60], "k": 5})
        assert embedder.calls[-1] == [(50, 60)]  # a hand-drawn box is embedded on demand
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

    sofa = Detection((0, 0, 960, 800), "sofa", 0.33)  # the couch the photo was taken on
    assert [d.label for d in clean([*found, sofa], 960, 800)] == ["mobile phone", "shoe"]
    assert [d.label for d in clean([sofa], 960, 800)] == ["sofa"]  # alone, it is the object
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0


def test_detection_runs_once_per_photo(database_url):
    class CountingDetector(FakeDetector):
        calls = 0

        def detect(self, image):
            CountingDetector.calls += 1
            return super().detect(image)

    with make_client(database_url, CountingDetector()) as client:
        photo = client.post("/api/photos", files={"file": ("room.jpg", jpeg(), "image/jpeg")}).json()
        first = client.post(f"/api/photos/{photo['photo_id']}/objects").json()
        again = client.post(f"/api/photos/{photo['photo_id']}/objects").json()
        assert first == again and CountingDetector.calls == 1


def test_limits_return_429_with_a_message(database_url):
    limits = Limits(photos_per_minute=1, searches_per_minute=2, photos_per_day=0)
    with make_client(database_url, limits=limits) as client:
        upload = lambda: client.post("/api/photos", files={"file": ("room.jpg", jpeg(), "image/jpeg")})
        photo = upload().json()
        blocked = upload()
        assert blocked.status_code == 429
        assert "1 new photo a minute" in blocked.json()["detail"] and int(blocked.headers["Retry-After"]) > 0

        search = lambda: client.post(f"/api/photos/{photo['photo_id']}/search", json={"k": 1})
        assert [search().status_code for _ in range(3)] == [200, 200, 429]
