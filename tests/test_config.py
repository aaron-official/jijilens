from urllib.parse import parse_qs, urlparse

import pytest

from jijilens.collect.discover import listing_api_url
from jijilens.config import DEFAULT_CONFIG, CollectConfig


def test_shipped_config_loads():
    config = CollectConfig.load(DEFAULT_CONFIG)
    keys = [c.key for c in config.categories]
    assert len(keys) == len(set(keys))
    assert all(c.jiji_slug for c in config.categories)


def test_category_key_defaults_to_slug_and_filters_are_kept(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(
        'categories = [\n'
        '  { slug = "mobile-phones" },\n'
        '  { key = "sofas", slug = "furniture", label = "Sofas", filters = { filter_attr_248_type = "Sofas" } },\n'
        ']\n'
    )
    phones, sofas = CollectConfig.load(path).categories
    assert (phones.key, phones.label, phones.filters) == ("mobile-phones", "mobile-phones", {})
    assert (sofas.key, sofas.jiji_slug, sofas.filters) == ("sofas", "furniture", {"filter_attr_248_type": "Sofas"})


def test_duplicate_keys_rejected(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('categories = [{ slug = "furniture" }, { slug = "furniture" }]\n')
    with pytest.raises(ValueError, match="unique"):
        CollectConfig.load(path)


def test_listing_api_url_with_filters():
    url = listing_api_url("https://jiji.ug", "furniture", {"filter_attr_248_type": "Beds & Bed Frames"})
    query = parse_qs(urlparse(url).query)
    assert query["slug"] == ["furniture"]
    assert query["filter_attr_248_type"] == ["Beds & Bed Frames"]
