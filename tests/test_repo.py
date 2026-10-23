"""Against the docker compose database. Everything runs in a transaction that is
rolled back, so collected data is never touched. Skipped when the database is down."""

import psycopg
import pytest

from jijilens import db
from jijilens.collect import repo
from jijilens.collect.models import listing_from_advert, stub_from_list_item
from jijilens.config import Category, load_settings

BASE = "https://jiji.ug"


@pytest.fixture
def conn():
    try:
        connection = db.connect(load_settings().database_url)
    except psycopg.OperationalError as exc:
        pytest.skip(f"database not reachable: {exc}")
    db.migrate(connection)
    yield connection
    connection.rollback()
    connection.close()


def test_discover_then_details_then_image(conn, listing_page, advert):
    ids = repo.ensure_categories(conn, [Category("test-phones", "Test phones", "mobile-phones")])
    category_id = ids["test-phones"]
    stubs = [s for s in (stub_from_list_item(i, BASE) for i in listing_page["adverts_list"]["adverts"]) if s]

    assert repo.add_stubs(conn, category_id, stubs) == 2
    assert repo.add_stubs(conn, category_id, stubs) == 0  # idempotent
    assert repo.count_listings(conn, category_id) == 2

    row = next(r for r in repo.pending_details(conn) if r["source_id"] == advert["guid"])
    listing = listing_from_advert(advert, BASE)
    repo.save_listing(conn, row["id"], listing, listing.images[:2])
    repo.save_listing(conn, row["id"], listing, listing.images[:2])  # re-run does not duplicate images

    saved = conn.execute("SELECT * FROM listings WHERE id = %s", (row["id"],)).fetchone()
    assert saved["status"] == "detailed"
    assert saved["price_ugx"] == 3_200_000
    assert saved["attributes"]["Brand"] == "Samsung"
    assert "0772" not in saved["description"]

    images = [r for r in repo.pending_images(conn) if r["source_id"] == advert["guid"]]
    assert [r["source_image_id"] for r in images] == [100, 101]
    assert images[0]["category_key"] == "test-phones"

    repo.save_image(conn, images[0]["id"], status="ok", local_path="images/x.webp", sha256="ab" * 32, phash="0" * 16, size=10)
    assert repo.sha256_known(conn, "ab" * 32)
    assert not repo.sha256_known(conn, "cd" * 32)


def test_schema_has_no_personal_columns(conn):
    columns = {
        r["column_name"]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name IN ('listings', 'listing_images')"
        )
    }
    assert not {c for c in columns if any(word in c for word in ("seller", "user", "phone", "contact", "store"))}


def test_closed_listing_gets_no_images(conn, advert):
    category_id = repo.ensure_categories(conn, [Category("test-phones", "Test phones", "mobile-phones")])["test-phones"]
    advert["is_closed"] = True
    stub = stub_from_list_item({**advert, "guid": "TESTclosed"}, BASE)
    repo.add_stubs(conn, category_id, [stub])
    listing_id = conn.execute("SELECT id FROM listings WHERE source_id = 'TESTclosed'").fetchone()["id"]
    listing = listing_from_advert(advert, BASE)
    repo.save_listing(conn, listing_id, listing, listing.images)
    assert conn.execute("SELECT status FROM listings WHERE id = %s", (listing_id,)).fetchone()["status"] == "gone"
    assert conn.execute("SELECT count(*) AS n FROM listing_images WHERE listing_id = %s", (listing_id,)).fetchone()["n"] == 0


def test_embeddings_search_uses_the_models_hnsw_index(conn):
    import numpy as np

    from jijilens.search import store

    store.prepare(conn)
    category_id = repo.ensure_categories(conn, [Category("test-sofas", "Test sofas", "furniture")])["test-sofas"]
    conn.execute(
        """INSERT INTO listings (source_id, collection_category_id, title, source_url, status)
           VALUES ('TESTsofa', %s, 'Grey sofa', 'https://jiji.ug/x', 'detailed')""",
        (category_id,),
    )
    listing_id = conn.execute("SELECT id FROM listings WHERE source_id = 'TESTsofa'").fetchone()["id"]
    image_ids = [
        conn.execute(
            """INSERT INTO listing_images (listing_id, source_image_id, position, url, local_path, status)
               VALUES (%s, %s, %s, 'u', %s, 'ok') RETURNING id""",
            (listing_id, n, n, f"images/{n}.webp"),
        ).fetchone()["id"]
        for n in range(3)
    ]
    model_id = store.ensure_model(conn, "test-model-4d", 4)
    vectors = np.eye(4, dtype=np.float32)[:3]
    store.save_embeddings(conn, model_id, zip(image_ids, vectors))
    assert {r["id"] for r in store.pending_embeddings(conn, model_id)}.isdisjoint(image_ids)
    store.ensure_index(conn, model_id, 4)

    query = np.array([0.1, 0.9, 0, 0], dtype=np.float32)
    hits = store.nearest_images(conn, model_id, 4, query, limit=2, category="test-sofas")
    assert [h["image_id"] for h in hits] == [image_ids[1], image_ids[0]]
    hits = store.nearest_images(conn, model_id, 4, query, limit=1, exclude_image_id=image_ids[1])
    assert hits[0]["image_id"] == image_ids[0]

    conn.execute("SET LOCAL enable_seqscan = off")  # tiny table: make the planner show it can use the index
    plan = "\n".join(
        row["QUERY PLAN"]
        for row in conn.execute(
            f"EXPLAIN SELECT image_id FROM image_embeddings WHERE model_id = {model_id}"
            " ORDER BY embedding::vector(4) <=> '[0,1,0,0]' LIMIT 5"
        )
    )
    assert f"image_embeddings_model{model_id}_hnsw" in plan
