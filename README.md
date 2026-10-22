# JijiLens

Multi-object visual product search for [Jiji Uganda](https://jiji.ug). You upload a photo of a room or an outfit, tap one object in it, and get visually similar listings. See [`Building Visual Product Search for Jiji Uganda.md`](Building%20Visual%20Product%20Search%20for%20Jiji%20Uganda.md) for the full design.

**Status: milestone 0, the catalog collector.** It builds a demo catalog of product listings and photos in Postgres (with pgvector enabled for the embedding step that comes next).

## What is collected, and what isn't

| Kept | Never stored |
|---|---|
| Product photos (up to 5 per ad, main photo first) | Seller name, avatar, rating, user id |
| Title and description, with contact details scrubbed | Phone numbers (and none are revealed: no "Show contact") |
| Category, price, attributes (brand, condition, storage…) | Store addresses, chat links |
| Coarse region (e.g. "Central Division") | Emails, links, @handles found in the text |
| Posted/edited dates, the canonical ad URL | Tracking and analytics data |

The allowlist lives in [`src/jijilens/collect/models.py`](src/jijilens/collect/models.py). Every field not named there is ignored. Free text goes through [`privacy.scrub`](src/jijilens/collect/privacy.py), which replaces phone numbers, emails, links and handles with `[number]`, `[email]`, `[link]` and `[handle]`. Images that carry EXIF or XMP metadata (which can include GPS) are re-encoded without it.

## Data policy

- **Public pages only.** No login, no cookies, no contact reveal. `robots.txt` allows the pages read here.
- **Slow and single-threaded.** There is a random 2–5 s pause between page requests and 0.5–1.5 s between image downloads, with backoff on 429/5xx. The run stops after 8 failures in a row.
- **Link back.** Every listing keeps its `source_url`, so search results send people to Jiji.
- **Not redistributed.** `data/` and the database are a private research index for this demo. The repo ships code and small fake fixtures only.

## How it works

The collector reads the JSON that jiji.ug's own front end uses, so no browser is needed:

1. **discover:** `GET /api_web/v1/listing?slug=<category>&page=N` returns 24 ads a page plus `next_url`. New ads are saved as `discovered`, up to `max_listings_per_category`.
2. **details:** each ad page's HTML embeds its data in `<script id="__NUXT_DATA__">`, a Nuxt "devalue" payload ([`nuxt.py`](src/jijilens/collect/nuxt.py) decodes it). The allowlisted fields are stored and the photos are queued.
3. **images:** each photo is downloaded at 1200 px, checked (readable, at least 200 px on its short side, not a flat placeholder), stripped of metadata, hashed (SHA-256 for exact duplicates, pHash for near duplicates) and saved under `data/images/<category>/<listing>/<image id>.webp`.

Every phase takes its work from the database, so an interrupted run resumes where it stopped.

## Running it

Requires Docker and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
docker compose up -d db              # Postgres 16 + pgvector on localhost:5433
uv sync
uv run jijilens db migrate

# smoke run: 5 listings per category
uv run jijilens collect all --max-listings 5
uv run jijilens stats

# the real catalog (~300 listings x 14 categories; about 9 hours at this pace)
uv run jijilens collect all
uv run jijilens export               # data/export/listings.jsonl + images.jsonl
```

Each phase can also run on its own (`collect discover`, `collect details --limit 100`, `collect images`). Categories, caps and pacing live in [`config/collect.toml`](config/collect.toml).

## Tests

```bash
uv run pytest
```

The database tests run against the compose database inside a rolled-back transaction, and are skipped when it is down.

## Layout

```
config/collect.toml          categories, caps, pacing
db/migrations/               schema (categories, listings, listing_images)
src/jijilens/cli.py          jijilens db | collect | stats | export
src/jijilens/collect/
  http.py                    polite client: pacing, retries, stop on repeated failure
  discover.py                phase 1, listing API
  details.py                 phase 2, ad pages
  nuxt.py                    devalue decoder for __NUXT_DATA__
  models.py                  the allowlist
  privacy.py                 contact-detail scrubbing
  images.py                  phase 3, download, validate, strip metadata, hash
  repo.py                    SQL
tests/                       unit tests with fake fixtures
```

## Next

Milestone 1 (retrieval only) embeds every image with OpenCLIP/SigLIP into a `vector` column with an HNSW index, and supports search from a manual crop.
