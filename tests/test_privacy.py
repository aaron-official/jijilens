import pytest

from jijilens.collect.privacy import scrub


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Call 0772 123 456 now", "Call [number] now"),
        ("WhatsApp +256-701-234567", "WhatsApp [number]"),
        ("tel 256772123456.", "tel [number]."),
        ("(0772) 123-456", "[number]"),
        ("0 7 7 2 1 2 3 4 5 6", "[number]"),
        ("0772123456 / 0701234567", "[number]"),
        ("mail me at shop.ug@example.co.ug", "mail me at [email]"),
        ("follow @test_shop_ug on IG", "follow [handle] on IG"),
        ("see https://example.com/x?y=1 and www.shop.ug", "see [link]"),  # repeats collapse
    ],
)
def test_contact_details_are_removed(text, expected):
    assert scrub(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Price 3,200,000 negotiable",
        "USh 12,500,000",
        "Samsung SM-G998B 256GB, 12GB RAM",
        "Size 42, 180 x 200 cm",
        "Model 2023, 12 months warranty",
    ],
)
def test_product_details_survive(text):
    assert scrub(text) == text


def test_empty():
    assert scrub(None) is None
    assert scrub("") == ""
