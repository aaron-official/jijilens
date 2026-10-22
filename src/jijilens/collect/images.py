"""Phase 3: download each queued image, check it, and store it.

Images are saved as `<DATA_DIR>/images/<category key>/<listing guid>/<image id>.<ext>`.
The original bytes are kept unless the file carries EXIF or XMP metadata
(which can hold GPS coordinates or device details). Such files are
re-encoded without it: losslessly for WebP and PNG, at quality 95 for JPEG.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx
import imagehash
import psycopg
from PIL import Image, UnidentifiedImageError

from jijilens.collect import repo
from jijilens.collect.http import NotFound, PoliteClient
from jijilens.config import CollectConfig

EXTENSIONS = {"WEBP": "webp", "JPEG": "jpg", "PNG": "png"}
MAX_PIXELS = 40_000_000  # far above Jiji's 1200x1600; guards against decompression bombs


class ImageRejected(Exception):
    """The file is not a usable product photo."""


@dataclass(frozen=True)
class ProcessedImage:
    data: bytes
    extension: str
    width: int
    height: int
    sha256: str
    phash: str
    metadata_stripped: bool


def process_image(raw: bytes, min_side: int) -> ProcessedImage:
    """Validate an image and drop its metadata. Raises ImageRejected."""
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            width, height = probe.size
            if width * height > MAX_PIXELS:
                raise ImageRejected(f"too large: {width}x{height}")
            probe.load()  # decode fully: catches truncated files
            image = probe.copy()
            info = dict(probe.info)
    except ImageRejected:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ImageRejected(f"not a readable image: {exc}") from exc

    if fmt not in EXTENSIONS:
        raise ImageRejected(f"unsupported format: {fmt}")
    if min(width, height) < min_side:
        raise ImageRejected(f"too small: {width}x{height}")
    if _is_blank(image):
        raise ImageRejected("blank image")

    data, stripped = raw, False
    if _has_metadata(image, info):
        data, stripped = _without_metadata(image, fmt, info), True

    return ProcessedImage(
        data=data,
        extension=EXTENSIONS[fmt],
        width=width,
        height=height,
        sha256=hashlib.sha256(data).hexdigest(),
        phash=str(imagehash.phash(image)),
        metadata_stripped=stripped,
    )


def _is_blank(image: Image.Image) -> bool:
    """One flat colour: a placeholder, not a photo."""
    extrema = image.getextrema()
    if not isinstance(extrema[0], tuple):  # single-band images give one (min, max)
        extrema = (extrema,)
    return all(low == high for low, high in extrema)


def _has_metadata(image: Image.Image, info: dict) -> bool:
    return bool(info.get("exif") or info.get("xmp") or info.get("XML:com.adobe.xmp") or len(image.getexif()))


def _without_metadata(image: Image.Image, fmt: str, info: dict) -> bytes:
    out = io.BytesIO()
    options: dict = {}
    if info.get("icc_profile"):
        options["icc_profile"] = info["icc_profile"]  # colour profile, not personal
    if fmt == "WEBP":
        options.update(lossless=True)
    elif fmt == "JPEG":
        options.update(quality=95)
    image.save(out, format=fmt, **options)
    return out.getvalue()


def download_images(
    conn: psycopg.Connection,
    client: PoliteClient,
    config: CollectConfig,
    data_dir: Path,
    *,
    limit: int | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    rows = repo.pending_images(conn, limit)
    log(f"images to download: {len(rows)}")
    counts = {"ok": 0, "rejected": 0, "failed": 0}
    for n, row in enumerate(rows, 1):
        prefix = f"  [{n}/{len(rows)}]"
        try:
            response = client.get(row["url"], delay=config.http.image_delay_seconds, accept="image/*")
        except NotFound:
            repo.save_image(conn, row["id"], status="failed", reason="not found")
            conn.commit()
            counts["failed"] += 1
            log(f"{prefix} not found: {row['url']}")
            continue
        except httpx.HTTPError as exc:
            repo.save_image(conn, row["id"], status="failed", reason=f"{type(exc).__name__}: {exc}")
            conn.commit()
            counts["failed"] += 1
            log(f"{prefix} failed: {row['url']}: {exc}")
            continue

        try:
            image = process_image(response.content, config.min_image_side)
        except ImageRejected as exc:
            repo.save_image(conn, row["id"], status="rejected", reason=str(exc))
            conn.commit()
            counts["rejected"] += 1
            log(f"{prefix} rejected ({exc}): {row['url']}")
            continue

        if repo.sha256_known(conn, image.sha256):
            repo.save_image(
                conn, row["id"], status="rejected", reason="duplicate", sha256=image.sha256, phash=image.phash
            )
            conn.commit()
            counts["rejected"] += 1
            log(f"{prefix} duplicate of an image already stored")
            continue

        relative = Path("images") / row["category_key"] / row["source_id"] / f"{row['source_image_id']}.{image.extension}"
        target = data_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(image.data)
        tmp.replace(target)
        repo.save_image(
            conn,
            row["id"],
            status="ok",
            local_path=relative.as_posix(),
            width=image.width,
            height=image.height,
            sha256=image.sha256,
            phash=image.phash,
            size=len(image.data),
        )
        conn.commit()
        counts["ok"] += 1
        note = ", metadata stripped" if image.metadata_stripped else ""
        log(f"{prefix} {image.width}x{image.height} {len(image.data) // 1024} KB{note}")
    return counts
