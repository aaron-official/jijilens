import dataclasses
import json
from datetime import timezone

from jijilens.collect.details import parse_ad_page
from jijilens.collect.models import canonical_url, listing_from_advert, stub_from_list_item
from tests.conftest import ad_page_html

BASE = "https://jiji.ug"
PERSONAL = ["FAKE SELLER NAME", "FAKE STORE", "avatar", "1234567", "0772999888", "/chat/start", "0772", "0701", "0700000000", "@example", "@test_shop"]


def test_listing_keeps_product_data(advert):
    listing = listing_from_advert(advert, BASE)
    assert listing.source_id == "TESTguid0000000000000001"
    assert listing.jiji_id == 9000001
    assert listing.title == "Samsung Galaxy S23 Ultra 256GB Black"
    assert listing.price_ugx == 3_200_000
    assert listing.price_text == "USh 3,200,000"
    assert listing.jiji_category_slug == "mobile-phones"
    assert listing.region == "Central Division"
    assert listing.source_url == "https://jiji.ug/central-division/mobile-phones/samsung-galaxy-s23-ultra-TESTguid0000000000000001.html"
    assert listing.attributes["Brand"] == "Samsung"
    assert listing.attributes["Storage"] == "256 GB"
    assert listing.attributes["Features"] == "NFC, Fingerprint, IP68"
    assert "Bulk prices" not in json.dumps(listing.attributes)  # nameless attribute dropped
    assert listing.posted_at.tzinfo is not None and listing.posted_at.astimezone(timezone.utc).year == 2026
    assert listing.is_active
    assert "Price 3,200,000 negotiable" in listing.description


def test_listing_drops_everything_personal(advert):
    listing = listing_from_advert(advert, BASE)
    stored = json.dumps(dataclasses.asdict(listing), default=str)
    for value in PERSONAL:
        assert value not in stored, value
    field_names = {f.name for f in dataclasses.fields(listing)}
    assert not field_names & {"seller", "user_id", "user_phone", "stores", "message_url"}


def test_images_main_first_and_placeholders_dropped(advert):
    images = listing_from_advert(advert, BASE).images
    assert [i.source_image_id for i in images] == [100, 101, 102]
    assert [i.position for i in images] == [0, 1, 2]
    assert images[0].is_main and not images[1].is_main
    assert images[2].width == 900


def test_closed_ad_is_inactive(advert):
    advert["is_closed"] = True
    assert not listing_from_advert(advert, BASE).is_active


def test_parse_ad_page_end_to_end(advert):
    listing = parse_ad_page(ad_page_html(advert), BASE)
    assert listing.source_id == advert["guid"]
    assert len(listing.images) == 3


def test_stubs_from_listing_page(listing_page):
    stubs = [stub_from_list_item(item, BASE) for item in listing_page["adverts_list"]["adverts"]]
    assert stubs[2] is None  # job ads are skipped
    assert stubs[0].source_url.endswith("TESTguid0000000000000001.html")
    assert "?" not in stubs[0].source_url
    assert stubs[1].title == "iPhone 12 call [number]"


def test_canonical_url():
    assert canonical_url("/a/b.html?page=2#x", BASE) == "https://jiji.ug/a/b.html"
    assert canonical_url("https://jiji.ug/a.html?lid=1", BASE) == "https://jiji.ug/a.html"
