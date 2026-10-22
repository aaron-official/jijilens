"""`jijilens` command line.

    jijilens db migrate
    jijilens collect discover|details|images|all [--limit N] [--max-listings N]
    jijilens stats
    jijilens export [--out DIR]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from jijilens import db
from jijilens.collect import repo
from jijilens.collect.details import fetch_details
from jijilens.collect.discover import discover
from jijilens.collect.http import PoliteClient, TooManyFailures
from jijilens.collect.images import download_images
from jijilens.config import DEFAULT_CONFIG, CollectConfig, load_settings


def log(message: str) -> None:
    print(message, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jijilens", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    db_parser = sub.add_parser("db", help="database tasks")
    db_parser.add_argument("action", choices=["migrate"])

    collect = sub.add_parser("collect", help="collect listings and images from jiji.ug")
    collect.add_argument("phase", choices=["discover", "details", "images", "all"])
    collect.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    collect.add_argument("--limit", type=int, help="at most this many ads (details) or images (images) this run")
    collect.add_argument("--max-listings", type=int, help="override max_listings_per_category (e.g. 5 for a smoke run)")

    sub.add_parser("stats", help="what has been collected so far")

    export = sub.add_parser("export", help="write listings.jsonl and images.jsonl for the embedding step")
    export.add_argument("--out", type=Path, help="default: <DATA_DIR>/export")

    args = parser.parse_args(argv)
    settings = load_settings()
    with db.connect(settings.database_url) as conn:
        if args.command == "db":
            applied = db.migrate(conn)
            log(f"applied: {', '.join(applied)}" if applied else "database is up to date")
            return 0
        if args.command == "collect":
            return _collect(conn, settings, args)
        if args.command == "stats":
            return _stats(conn)
        if args.command == "export":
            return _export(conn, args.out or settings.data_dir / "export")
    return 1


def _collect(conn, settings, args) -> int:
    config = CollectConfig.load(args.config)
    if args.max_listings is not None:
        config = dataclasses.replace(config, max_listings_per_category=args.max_listings)
    phases = ["discover", "details", "images"] if args.phase == "all" else [args.phase]
    try:
        with PoliteClient(config.http, log=log) as client:
            for phase in phases:
                log(f"== {phase} ==")
                if phase == "discover":
                    log(f"added {discover(conn, client, config, log=log)} listing(s)")
                elif phase == "details":
                    log(f"done: {fetch_details(conn, client, config, limit=args.limit, log=log)}")
                else:
                    counts = download_images(conn, client, config, settings.data_dir, limit=args.limit, log=log)
                    log(f"done: {counts}")
    except TooManyFailures as exc:
        log(f"stopped: {exc}")
        return 2
    except KeyboardInterrupt:
        conn.rollback()
        log("\ninterrupted; everything saved so far is kept, run the same command to resume")
        return 130
    return 0


def _stats(conn) -> int:
    rows = repo.stats(conn)
    columns = ["key", "listings", "detailed", "discovered", "gone", "failed", "images_ok", "images_pending", "images_rejected", "images_failed"]
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) if rows else len(c) for c in columns}
    print("  ".join(c.ljust(widths[c]) for c in columns))
    for row in rows:
        print("  ".join(str(row[c]).ljust(widths[c]) for c in columns))
    total_bytes = sum(r["image_bytes"] for r in rows)
    print(f"\n{sum(r['listings'] for r in rows)} listings, {sum(r['images_ok'] for r in rows)} images, {total_bytes / 1e6:.1f} MB")
    return 0


def _export(conn, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("listings", repo.export_listings(conn)), ("images", repo.export_images(conn))):
        path = out_dir / f"{name}.jsonl"
        n = 0
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, default=_json_default, ensure_ascii=False) + "\n")
                n += 1
        log(f"{n} {name} -> {path}")
    return 0


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


if __name__ == "__main__":
    sys.exit(main())
