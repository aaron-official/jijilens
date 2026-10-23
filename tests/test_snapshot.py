"""Snapshot export and load, between two throwaway databases on the compose server."""

import numpy as np
import psycopg
import pytest

from jijilens import db, snapshot
from jijilens.collect import repo
from jijilens.config import Category
from jijilens.search import store


@pytest.fixture
def make_db(new_database):
    connections = []

    def make() -> psycopg.Connection:
        conn = db.connect(new_database())
        store.prepare(conn)
        connections.append(conn)
        return conn

    yield make
    for conn in connections:
        conn.close()


def test_export_then_load_into_an_empty_database(make_db, tmp_path):
    source = make_db()
    category_id = repo.ensure_categories(source, [Category("sofas", "Sofas", "furniture")])["sofas"]
    for n, (status, active) in enumerate([("detailed", True), ("detailed", False), ("discovered", True)]):
        source.execute(
            """INSERT INTO listings (source_id, collection_category_id, title, description, source_url, status, is_active, price_ugx)
               VALUES (%s, %s, %s, 'private notes', 'https://jiji.ug/x', %s, %s, 1000)""",
            (f"L{n}", category_id, f"Sofa {n}", status, active),
        )
    listing_ids = [r["id"] for r in source.execute("SELECT id FROM listings ORDER BY id")]
    image_ids = [
        source.execute(
            """INSERT INTO listing_images (listing_id, source_image_id, position, url, local_path, status)
               VALUES (%s, %s, 0, 'https://pictures.jiji.ug/x.webp', 'images/x.webp', 'ok') RETURNING id""",
            (listing_id, n),
        ).fetchone()["id"]
        for n, listing_id in enumerate(listing_ids)
    ]
    model_id = store.ensure_model(source, "test-4d", 4)
    store.save_embeddings(source, model_id, zip(image_ids, np.eye(4, dtype=np.float32)[:3]))
    source.commit()

    manifest = snapshot.export(source, tmp_path, log=lambda m: None)
    assert manifest["rows"]["listings"] == 1  # only the detailed, active listing
    assert manifest["rows"]["image_embeddings"] == 1

    target = make_db()
    assert snapshot.is_empty(target)
    snapshot.load(target, tmp_path, log=lambda m: None)
    listing = target.execute("SELECT * FROM listings").fetchone()
    assert (listing["title"], listing["price_ugx"], listing["description"]) == ("Sofa 0", 1000, None)
    assert target.execute("SELECT local_path FROM listing_images").fetchone()["local_path"] is None

    hits = store.nearest_images(target, model_id, 4, np.array([1, 0, 0, 0], dtype=np.float32), limit=5)
    assert [h["image_id"] for h in hits] == [image_ids[0]]

    # New rows get fresh ids after a load.
    repo.ensure_categories(target, [Category("chairs", "Chairs", "furniture")])
    with pytest.raises(RuntimeError):
        snapshot.load(target, tmp_path, log=lambda m: None)
