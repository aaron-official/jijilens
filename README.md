# JijiLens

Multi-object visual product search for [Jiji Uganda](https://jiji.ug). You upload a photo of a room or an outfit, tap one object in it, and get visually similar listings. See [`Building Visual Product Search for Jiji Uganda.md`](Building%20Visual%20Product%20Search%20for%20Jiji%20Uganda.md) for the full design.

**Status: milestone 2, the web app.** Milestone 0 collected a demo catalog of 4,134 listings and 10,166 photos across 14 categories. Milestone 1 embedded them into pgvector and measured search quality. Milestone 2 is a web page: upload a photo, the objects in it are outlined, and tapping one (or drawing a box) shows matching listings that link to Jiji.

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

Then embed the photos and search:

```bash
uv run jijilens embed                # SigLIP 2 vectors + HNSW index (resumable; ~3 h on an old dual-core CPU)
uv run jijilens search room.jpg --crop 120,340,860,900 --category sofas
uv run jijilens eval                 # Recall@K per category, threshold calibration, HNSW vs exact
```

And the web app, on http://127.0.0.1:8765:

```bash
uv run jijilens serve                # loads both models once (~1-2 min on an old CPU), then ~1 s a search
uv run jijilens serve --detector none   # draw boxes by hand only; starts faster
```

Each collection phase can also run on its own (`collect discover`, `collect details --limit 100`, `collect images`). Categories, caps and pacing live in [`config/collect.toml`](config/collect.toml).

## Search

- **Model.** [SigLIP 2](https://arxiv.org/abs/2502.14786) ViT-B/16 at 224 px through `open_clip`, on CPU-only PyTorch. Each photo becomes a 768-number vector, L2-normalised so cosine similarity ranks matches. `--model mobileclip2-s0` swaps in Apple's MobileCLIP2-S0 (512 numbers, about 5x faster, lower quality) for comparison. Vectors from both can live side by side.
- **Storage.** `image_embeddings` holds one row per photo per model in an unsized `vector` column. Each model gets its own HNSW index over `embedding::vector(dim)`, limited to its rows. pgvector was chosen over FAISS or a hosted vector database because the listings already live in Postgres: one query does the nearest-neighbour search, filters (category, price, active only) and the join to titles and links, and closed listings drop out of results in the same transaction. At 10k vectors (~31 MB) speed doesn't decide it; `eval` measures it anyway.
- **Ranking.** A search fetches the nearest photos, keeps each listing's best one, and returns one result per listing, so an ad with five photos can't fill the list.
- **Evaluation.** `jijilens eval` is leave-one-out: each listing's second photo searches the whole catalog, and Recall@K is how often that listing comes back in the top K. Another seller's ad for the same product counts as a miss, so this is a lower bound. Each query's best match in a *different* category stands in for an out-of-catalog search, which calibrates the "no close matches" threshold. A sample of queries is also run through HNSW and through exact numpy search to report the index's recall and latency. Reports go to `data/eval/`.

## Web app

- **One process** ([`api/app.py`](src/jijilens/api/app.py)) loads SigLIP 2 and the detector once and serves both the JSON API and the page ([`api/static/`](src/jijilens/api/static), plain HTML/CSS/JS, no build step). Uploads stay in memory (the last 50) and are never written to disk.
- **Detection only proposes boxes.** [OmDet-Turbo](https://huggingface.co/omlab/omdet-turbo-swin-tiny-hf) finds objects from a list of names. Its labels are unreliable on phone photos, so the catalog search decides what the object is. On a 2012 dual-core CPU it takes 5-9 s per photo, against ~31 s for Grounding DINO tiny and ~4 s for OWL-ViT B/32, which missed objects. The page runs it in the background; you can draw a box at any time.
- **No close match.** If the best result is below the model's calibrated similarity (0.80 for SigLIP 2), the page says so and labels the results as the nearest the catalog has. A mouse or a TV remote, neither of which the catalog stocks, gets this state; a JBL speaker returns a JBL Xtreme first.
- Result photos load from Jiji's own image links, and every result links to the listing.

## Docker

One image holds the app, both models (baked in at build time) and its own Postgres 17 + pgvector. At startup it loads a **snapshot** of the searchable catalog into that database and serves on port 7860. Nothing outside the container is needed.

```bash
uv run jijilens snapshot export      # data/snapshot/, ~37 MB: listings, image urls, vectors
docker build -t jijilens .
docker run -p 7860:7860 -v "$PWD/data/snapshot:/snapshot:ro" jijilens
```

The snapshot holds only what the demo shows (titles, prices, regions, links, Jiji image urls and vectors), never descriptions, attributes or photo files. It is not baked into the image: it is mounted, or with `SNAPSHOT_REPO` (and `HF_TOKEN`) set, downloaded from a private Hugging Face dataset at startup, which is how a Hugging Face Space runs it. The image is about 3.8 GB, half of it model weights; startup takes about 1.5 minutes on an old dual-core CPU.

## Tests

```bash
uv run pytest
```

The database tests run against the compose database inside a rolled-back transaction, and are skipped when it is down.

## Layout

```
config/collect.toml          categories, caps, pacing
db/migrations/               schema (categories, listings, listing_images, image_embeddings)
src/jijilens/cli.py          jijilens db | collect | stats | export | embed | search | eval | serve
src/jijilens/collect/
  http.py                    polite client: pacing, retries, stop on repeated failure
  discover.py                phase 1, listing API
  details.py                 phase 2, ad pages
  nuxt.py                    devalue decoder for __NUXT_DATA__
  models.py                  the allowlist
  privacy.py                 contact-detail scrubbing
  images.py                  phase 3, download, validate, strip metadata, hash
  repo.py                    SQL
src/jijilens/api/
  app.py                     FastAPI: upload, detect, search; serves the page
  static/                    the page (HTML, CSS, JS)
src/jijilens/search/
  detector.py                open-vocabulary object detection (OmDet-Turbo, Grounding DINO)
  embedder.py                models (SigLIP 2, MobileCLIP2) and image -> vector
  embed.py                   embed the catalog, then build the HNSW index
  store.py                   pgvector SQL: save, index, nearest images
  query.py                   crop, search, one result per listing
  evaluate.py                leave-one-out Recall@K, calibration, HNSW vs exact
tests/                       unit tests with fake fixtures
```

## Next

Deployment: one Docker image (app + models) on a Hugging Face Space, with the catalog in a hosted Postgres with pgvector. Then milestone 3: fine-tune the embedding model on listing photos and re-rank with listing text and attributes.
