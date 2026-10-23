import numpy as np
import pytest
from PIL import Image

from jijilens.search.evaluate import Catalog, exact_results, query_rows, summarize
from jijilens.search.query import load_query_image, parse_box, rank_listings


def hit(listing_id, similarity, image_id=0, category="sofas"):
    return {"listing_id": listing_id, "similarity": similarity, "image_id": image_id, "local_path": "", "category": category}


def test_rank_listings_keeps_each_listings_best_image():
    hits = [hit(1, 0.9, 10), hit(1, 0.95, 11), hit(2, 0.92, 20), hit(3, 0.5, 30)]
    ranked = rank_listings(hits, k=2)
    assert [(m.listing_id, m.similarity, m.image_id) for m in ranked] == [(1, 0.95, 11), (2, 0.92, 20)]


def test_parse_box():
    assert parse_box("10,20,110,220") == (10, 20, 110, 220)
    assert parse_box("10.4, 20.6, 30, 40") == (10, 21, 30, 40)
    for bad in ("1,2,3", "a,b,c,d", "50,0,10,10"):
        with pytest.raises(ValueError):
            parse_box(bad)


def test_load_query_image_crops_and_clamps(tmp_path):
    path = tmp_path / "room.png"
    Image.new("RGB", (400, 300), "white").save(path)
    assert load_query_image(path).size == (400, 300)
    assert load_query_image(path, (100, 50, 300, 250)).size == (200, 200)
    assert load_query_image(path, (300, 200, 900, 900)).size == (100, 100)  # clamped to the image
    with pytest.raises(ValueError):
        load_query_image(path, (500, 500, 600, 600))


def test_load_query_image_applies_exif_rotation(tmp_path):
    path = tmp_path / "phone.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90 degrees
    Image.new("RGB", (400, 300)).save(path, exif=exif)
    assert load_query_image(path).size == (300, 400)


def catalog():
    """Three listings: two sofas that look alike, and a phone. Listing 3 has one photo."""
    vectors = np.array(
        [[1, 0, 0], [0.99, 0.14, 0], [0.9, 0.43, 0], [0.88, 0.47, 0], [0, 0, 1]], dtype=np.float32
    )
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    return Catalog(
        image_ids=np.array([10, 11, 20, 21, 30]),
        listing_ids=np.array([1, 1, 2, 2, 3]),
        positions=np.array([0, 1, 0, 1, 0]),
        categories=np.array(["sofas", "sofas", "sofas", "sofas", "phones"]),
        vectors=vectors,
    )


def test_query_rows_are_second_photos_of_multi_photo_listings():
    assert list(query_rows(catalog())) == [1, 3]


def test_exact_results_finds_own_listing_and_never_the_query_itself():
    records = exact_results(catalog(), query_rows(catalog()), k=2)
    by_image = {r["image_id"]: r for r in records}
    assert by_image[11]["rank"] == 1  # image 11's nearest other image is 10, same listing
    assert by_image[21]["rank"] == 1
    assert by_image[11]["top1_category"] == "sofas"
    assert by_image[11]["other_category_similarity"] == pytest.approx(0.0, abs=1e-6)

    summary = summarize(records, ks=(1,))
    assert summary["overall"] == {"queries": 2, "recall@1": 1.0, "category@1": 1.0}
    assert summary["categories"]["sofas"]["queries"] == 2
