"""`jijilens` command line.

    jijilens db migrate
    jijilens collect discover|details|images|all [--limit N] [--max-listings N]
    jijilens stats
    jijilens export [--out DIR]
    jijilens embed [--model NAME] [--limit N] [--batch-size N]
    jijilens search PHOTO [--crop x1,y1,x2,y2] [--category KEY] [-k N] [--model NAME]
    jijilens eval [--model NAME] [--hnsw-sample N]
    jijilens serve [--host H] [--port N] [--model NAME] [--detector NAME|none]
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
from jijilens.search import store
from jijilens.search.detector import DEFAULT_DETECTOR, DETECTORS
from jijilens.search.embedder import DEFAULT_MODEL, MODELS, get_spec


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

    embed = sub.add_parser("embed", help="embed downloaded images and build the vector index")
    embed.add_argument("--model", choices=list(MODELS), default=DEFAULT_MODEL)
    embed.add_argument("--limit", type=int, help="at most this many images this run")
    embed.add_argument("--batch-size", type=int, default=16)

    search = sub.add_parser("search", help="find listings that look like a photo")
    search.add_argument("photo", type=Path)
    search.add_argument("--crop", help="x1,y1,x2,y2 in pixels: search with just this part of the photo")
    search.add_argument("--category", help="only this collection category, e.g. sofas")
    search.add_argument("-k", type=int, default=10, help="how many listings to show")
    search.add_argument("--model", choices=list(MODELS), default=DEFAULT_MODEL)
    search.add_argument("--min-similarity", type=float, help="below this, report no close matches")

    evaluate = sub.add_parser("eval", help="measure retrieval quality and index speed")
    evaluate.add_argument("--model", choices=list(MODELS), default=DEFAULT_MODEL)
    evaluate.add_argument("--hnsw-sample", type=int, default=200, help="queries to compare HNSW against exact search")
    evaluate.add_argument("--out", type=Path, help="default: <DATA_DIR>/eval")

    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--model", choices=list(MODELS), default=DEFAULT_MODEL)
    serve.add_argument("--detector", choices=[*DETECTORS, "none"], default=DEFAULT_DETECTOR)

    args = parser.parse_args(argv)
    if args.command == "serve":
        return _serve(load_settings(), args)
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
        if args.command == "embed":
            return _embed(conn, settings, args)
        if args.command == "search":
            return _search(conn, settings, args)
        if args.command == "eval":
            return _eval(conn, args, args.out or settings.data_dir / "eval")
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


def _embed(conn, settings, args) -> int:
    from jijilens.search.embed import embed_catalog
    from jijilens.search.embedder import Embedder

    spec = get_spec(args.model)
    log(f"loading {spec.name} ({spec.arch})")
    store.prepare(conn)
    try:
        counts = embed_catalog(conn, Embedder(spec), settings.data_dir, batch_size=args.batch_size, limit=args.limit, log=log)
    except KeyboardInterrupt:
        conn.rollback()
        log("\ninterrupted; embedded batches are kept, run the same command to resume")
        return 130
    log(f"done: {counts}")
    return 0


def _search(conn, settings, args) -> int:
    from jijilens.search.embedder import Embedder
    from jijilens.search.query import load_query_image, parse_box, search

    spec = get_spec(args.model)
    model_id = store.model_id(conn, spec.name)
    if model_id is None:
        log(f"no {spec.name} embeddings yet; run: jijilens embed --model {spec.name}")
        return 1
    image = load_query_image(args.photo, parse_box(args.crop) if args.crop else None)
    store.prepare(conn)
    matches = search(conn, Embedder(spec), model_id, image, k=args.k, category=args.category)
    if args.min_similarity is not None:
        matches = [m for m in matches if m.similarity >= args.min_similarity]
    if not matches:
        log("no close matches")
        return 0
    cards = store.listing_cards(conn, [m.listing_id for m in matches])
    for n, match in enumerate(matches, 1):
        card = cards[match.listing_id]
        price = f"UGX {card['price_ugx']:,}" if card["price_ugx"] else (card["price_text"] or "no price")
        log(f"{n:>2}  {match.similarity:.3f}  {card['category']:<13} {price:<16} {card['title']}")
        log(f"    {card['source_url']}")
        log(f"    {settings.data_dir / match.local_path}")
    return 0


def _eval(conn, args, out_dir: Path) -> int:
    from jijilens.search.evaluate import evaluate

    spec = get_spec(args.model)
    model_id = store.model_id(conn, spec.name)
    if model_id is None:
        log(f"no {spec.name} embeddings yet; run: jijilens embed --model {spec.name}")
        return 1
    store.prepare(conn)
    report = {"model": spec.name, "run_at": datetime.now().isoformat(timespec="seconds")}
    report.update(evaluate(conn, model_id, spec.dim, hnsw_sample=args.hnsw_sample))

    retrieval = report["retrieval"]
    columns = [c for c in retrieval["overall"] if c != "queries"]
    log(f"{spec.name}: {report['images']} images, {report['listings']} listings\n")
    log(f"{'category':<15}{'queries':>8}" + "".join(f"{c:>12}" for c in columns))
    for name, row in [*retrieval["categories"].items(), ("ALL", retrieval["overall"])]:
        log(f"{name:<15}{row['queries']:>8}" + "".join(f"{row[c]:>12.3f}" for c in columns))
    log(f"\nsimilarity to own listing:      {report['calibration']['own_listing']}")
    log(f"best match in another category: {report['calibration']['other_category']}")
    log(f"HNSW vs exact: {report['hnsw']}")

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{spec.name}-{report['run_at'].replace(':', '')}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log(f"\nsaved {path}")
    return 0


def _serve(settings, args) -> int:
    import uvicorn

    from jijilens.api.app import create_app
    from jijilens.search.detector import Detector
    from jijilens.search.embedder import Embedder

    spec = get_spec(args.model)
    log(f"loading {spec.name}")
    embedder = Embedder(spec)
    detector = None
    if args.detector != "none":
        log(f"loading {args.detector}")
        detector = Detector(DETECTORS[args.detector])
    app = create_app(settings.database_url, embedder, detector)
    log(f"JijiLens on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral() else float(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


if __name__ == "__main__":
    sys.exit(main())
