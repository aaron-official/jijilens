import io

import pytest
from PIL import Image

from jijilens.collect.images import ImageRejected, process_image


def photo(fmt="WEBP", size=(300, 400), exif=False) -> bytes:
    image = Image.new("RGB", size)
    for x in range(0, size[0], 10):  # not a flat colour
        image.putpixel((x, x % size[1]), (x % 256, 100, 200))
    options = {}
    if exif:
        data = Image.Exif()
        data[0x010F] = "FakeCam"  # Make
        data[0x8825] = {2: (0.0, 19.0, 0.0)}  # GPSInfo
        options["exif"] = data.tobytes()
    if fmt == "WEBP":
        options["lossless"] = True
    out = io.BytesIO()
    image.save(out, format=fmt, **options)
    return out.getvalue()


def test_clean_image_is_kept_byte_for_byte():
    raw = photo()
    result = process_image(raw, min_side=200)
    assert result.data == raw
    assert (result.width, result.height, result.extension) == (300, 400, "webp")
    assert not result.metadata_stripped
    assert len(result.sha256) == 64 and len(result.phash) == 16


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP"])
def test_exif_is_stripped(fmt):
    raw = photo(fmt, exif=True)
    assert Image.open(io.BytesIO(raw)).getexif()  # the input does carry EXIF
    result = process_image(raw, min_side=200)
    assert result.metadata_stripped
    with Image.open(io.BytesIO(result.data)) as cleaned:
        assert not cleaned.getexif()
        assert cleaned.size == (300, 400)


def test_rejects_small_blank_and_broken():
    with pytest.raises(ImageRejected, match="too small"):
        process_image(photo(size=(150, 400)), min_side=200)
    blank = io.BytesIO()
    Image.new("RGB", (300, 300), "white").save(blank, format="PNG")
    with pytest.raises(ImageRejected, match="blank"):
        process_image(blank.getvalue(), min_side=200)
    with pytest.raises(ImageRejected, match="not a readable image"):
        process_image(b"<html>not an image</html>", min_side=200)
    with pytest.raises(ImageRejected):
        process_image(photo(fmt="JPEG")[:200], min_side=200)  # truncated
