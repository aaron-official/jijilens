# Building Visual Product Search for Jiji Uganda

## Direct answer

The feature is a **visual product search engine**, not a normal reverse-image lookup. Build it as two linked pipelines:

1. **Offline indexing:** collect authorized marketplace listings, download their product images, convert every image into an embedding vector, and store the vector beside the listing metadata.
2. **Online search:** accept a user's photo, detect or segment each product in it, let the user select one product, embed that crop, retrieve the closest listing vectors, then filter and re-rank the matches.

For a portfolio project, do **not** train a model from scratch. Start with pretrained detection/segmentation and image-embedding models, then fine-tune only if evaluation on Ugandan listings shows a clear failure. CLIP-style models already encode images and text into aligned vectors, while SAM 2 can produce object masks and Grounding DINO can localize objects from category prompts.[^1][^2][^3]

## What the feature does

A photo containing shoes, a handbag, a dress, and sunglasses should produce four selectable regions. Clicking the handbag should search handbag listings; clicking the shoes should issue a different vector query.

```text
Uploaded photo
      |
      v
Object detection / segmentation
      |
      +--> crop 1: handbag --> embedding --> nearest handbag listings
      +--> crop 2: shoes ----> embedding --> nearest shoe listings
      +--> crop 3: dress ----> embedding --> nearest dress listings
      +--> crop 4: glasses --> embedding --> nearest eyewear listings
```

Whole-image similarity alone is insufficient for this experience because a single embedding may emphasize the largest or most visually dominant object. Research has identified greater CLIP image-encoder sensitivity to larger objects in multi-object scenes, which supports detecting and cropping each object before retrieval.[^4]

## How large platforms do it

Alibaba's published visual-search architecture combines category prediction, object localization, learned visual features, large-scale indexing, retrieval, and re-ranking. Its system used category prediction to narrow the search space, a CNN for detection and feature learning, a binary index for scale, and behavior signals to improve ranking.[^5][^6]

Alibaba also described an offline process that builds the product index and an online process that recognizes a query image and retrieves the associated product data. Later work used co-click behavior—products users clicked during the same search—as training supervision, reducing dependence on manually annotated examples.[^7][^8]

The difference between that production system and a portfolio MVP is mostly scale, proprietary training data, and ranking intelligence—not the basic flow. Google describes the same core product-search abstraction: create products with reference images, index the product set, then return ranked visually and semantically similar products for a query image.[^9][^10]

## Recommended MVP stack

This fits a Next.js, FastAPI, PostgreSQL, and Docker workflow, with Python handling inference because the model ecosystem is strongest there.

| Layer | MVP choice | Why |
|---|---|---|
| Web interface | Next.js + React | Upload/camera UI, bounding-box overlay, selectable objects, responsive results |
| API | FastAPI | Simple Python integration with PyTorch, Transformers, and image libraries |
| Metadata database | PostgreSQL | Listings, images, categories, prices, locations, search logs |
| Vector search | PostgreSQL + pgvector | One database and simple deployment for an MVP |
| Object localization | Grounding DINO first; SAM 2 for mask refinement | DINO finds category-aware boxes; SAM 2 can create cleaner clickable object shapes |
| Product embeddings | OpenCLIP/SigLIP; FashionCLIP for fashion | Pretrained visual similarity without training from scratch |
| Image storage | S3-compatible storage such as Cloudflare R2 or MinIO locally | Store uploaded queries, listing images, and object crops |
| Background jobs | Celery/RQ + Redis, or a simple worker initially | Generate listing embeddings outside request handling |
| Deployment | Docker Compose locally; one affordable VPS plus optional GPU inference service | Keeps the portfolio deployment understandable and economical |

`pgvector` supports exact vector search and approximate HNSW/IVFFlat indexes; HNSW gives a strong speed–recall trade-off but uses more memory and takes longer to build. Qdrant is a good later alternative if vector search becomes a separate service; it supports nearest-neighbor queries and cosine similarity directly.[^11][^12][^13][^14]

## Model choices

### Detection and segmentation

Start with **Grounding DINO** and provide a vocabulary matching marketplace categories, for example:

```text
phone. laptop. television. shoe. dress. shirt. bag. watch.
chair. table. refrigerator. bicycle. motorcycle. car.
```

Grounding DINO is open-set: it accepts an image plus text categories and returns candidate bounding boxes with text-similarity scores. Add non-maximum suppression, remove tiny regions, and cap the UI at perhaps the strongest several objects so the image does not become covered in boxes.[^3][^15]

Use **SAM 2** only after a box has been found, or after the user clicks a point, to obtain a precise object mask. SAM 2 supports points, boxes, and masks as prompts and can automatically generate masks for images.[^16][^2]

An even simpler first milestone is to skip automatic detection and let the user draw a crop rectangle. This validates retrieval before adding the hardest part of the interface.

### Embedding model

For a mixed Jiji catalog, begin with a general model such as OpenCLIP or SigLIP. CLIP learns image and text encoders whose outputs can be compared with cosine similarity, enabling image-to-image and text-to-image retrieval.[^17][^1]

For a fashion-only demo, compare the general model against **Marqo-FashionCLIP** or FashionCLIP. FashionCLIP was trained on more than 700,000 fashion image-text pairs, while Marqo publishes fashion-specific models and loading examples through Transformers/OpenCLIP.[^18][^19]

Do not mix embeddings created by different models in one vector column. When changing models, version the embedding, create a new index, and re-embed the entire catalog.

## Data pipeline

Jiji does not appear to provide a public official bulk listings API; third-party services advertise extraction APIs for public Jiji listings, including Uganda, but their existence does not grant permission to republish or permanently index Jiji content. For a portfolio project, the safest route is a self-created or permission-based demo catalog that links back to the original listing and avoids seller phone numbers or other unnecessary personal data.[^20][^21]

A practical ingestion job should:

1. Receive listings from an authorized feed, manually prepared CSV, seller uploads, or a small permitted dataset.
2. Save `listing_id`, title, description, category, price, currency, location, source URL, status, and image URLs.
3. Download each product image and reject broken, tiny, duplicated, or unsafe files.
4. Normalize orientation and color mode, but preserve the uncropped original.
5. Generate one vector per listing image rather than only one vector per listing.
6. Save the vectors with `listing_id`, `image_id`, `category_id`, and `model_version`.
7. Re-index when a listing changes; delete or deactivate its vectors when it expires.

Multiple images matter because a seller may photograph the same phone from the front, rear, and side. Search all image vectors, then collapse duplicate hits by `listing_id`, keeping the highest score.

## Database design

Example schema for a 512-dimensional model:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE listings (
  id              BIGSERIAL PRIMARY KEY,
  source_id       TEXT UNIQUE NOT NULL,
  title           TEXT NOT NULL,
  description     TEXT,
  category_id     BIGINT,
  price_ugx       BIGINT,
  district        TEXT,
  source_url      TEXT NOT NULL,
  is_active       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE listing_images (
  id              BIGSERIAL PRIMARY KEY,
  listing_id      BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
  image_url       TEXT NOT NULL,
  embedding       vector(512),
  model_version   TEXT NOT NULL,
  UNIQUE (listing_id, image_url, model_version)
);

CREATE INDEX listing_images_embedding_hnsw
ON listing_images
USING hnsw (embedding vector_cosine_ops);
```

A category-constrained query can look like:

```sql
SELECT
  l.id,
  l.title,
  l.price_ugx,
  l.district,
  l.source_url,
  1 - (li.embedding <=> :query_vector) AS visual_score
FROM listing_images li
JOIN listings l ON l.id = li.listing_id
WHERE l.is_active = TRUE
  AND (:category_id IS NULL OR l.category_id = :category_id)
ORDER BY li.embedding <=> :query_vector
LIMIT 100;
```

pgvector defines cosine similarity as one minus cosine distance and supports an HNSW index using `vector_cosine_ops`.[^13]

## Search request flow

### API endpoints

```text
POST /api/visual-search/detect
  multipart image
  -> query_id, image dimensions, objects[]

POST /api/visual-search/query
  query_id + object_id or user crop coordinates + filters
  -> ranked listings[]

POST /api/visual-search/feedback
  query_id + selected listing_id + event(click/save/contact)
  -> 204
```

Example detection response:

```json
{
  "query_id": "q_8fa2",
  "width": 1080,
  "height": 1440,
  "objects": [
    {
      "id": "obj_1",
      "label": "handbag",
      "confidence": 0.87,
      "box": {"x": 0.10, "y": 0.31, "w": 0.37, "h": 0.42}
    },
    {
      "id": "obj_2",
      "label": "shoe",
      "confidence": 0.81,
      "box": {"x": 0.52, "y": 0.65, "w": 0.39, "h": 0.22}
    }
  ]
}
```

Use normalized coordinates from 0 to 1 so the React overlay works correctly as the image resizes.

### Backend logic

```python
async def visual_search(image, selected_box=None, filters=None):
    rgb = decode_and_validate(image)
    objects = detector.detect(rgb, category_prompts)

    if selected_box is None:
        return {"objects": objects}

    crop = crop_with_padding(rgb, selected_box, padding=0.08)
    query_vector = embedder.encode_image(crop, normalize=True)

    candidates = vector_store.search(
        vector=query_vector,
        category=selected_box.label,
        limit=100,
    )

    results = rerank(
        candidates,
        category=selected_box.label,
        filters=filters,
    )
    return deduplicate_by_listing(results)[:30]
```

Use the same resize, normalization, color conversion, and model checkpoint during ingestion and querying. A mismatch here can silently destroy retrieval quality.

## Ranking strategy

Nearest-neighbor similarity should retrieve candidates, not make the final business decision. A useful initial ranking formula is:

```text
final_score =
  0.65 * visual_similarity
+ 0.15 * category_match
+ 0.08 * text_similarity
+ 0.05 * location_match
+ 0.04 * listing_quality
+ 0.03 * freshness
```

These weights are starting hypotheses, not universal values. Log impressions, clicked object, returned rank, listing click, save, and contact events; once enough data exists, use them to tune weights or train a learning-to-rank model. Alibaba's published work similarly used click behavior and ranking objectives to improve visual-search relevance.[^22][^8]

Useful re-ranking rules include:

- Hard-filter inactive listings and obviously wrong categories.
- Apply user-selected price and location constraints.
- Penalize low-resolution, watermarked, collage, or misleading listing images.
- Collapse several matching images from one listing into one result.
- Diversify near-identical results so one seller does not occupy the entire first page.
- Keep promoted-listing logic separate from relevance and label promotion clearly.

## Build order

### Milestone 1: retrieval only

Create a lawful demo catalog of roughly a few thousand listing images across 5–10 visually distinct categories. Generate embeddings, store them in pgvector, and support manually cropped image search. This proves the fundamental retrieval loop without detection complexity.

### Milestone 2: multi-object UX

Add Grounding DINO, draw returned boxes in React, and let the user tap a region. Include “draw your own box” and “search the whole image” fallbacks when automatic detection is wrong.

### Milestone 3: better relevance

Add category filtering, listing-text embeddings, price/location filters, duplicate collapse, and a second-stage re-ranker. Compare general embeddings against a domain-specific model for fashion, electronics, or vehicles.

### Milestone 4: production polish

Add asynchronous ingestion, batch GPU embedding, model/version tracking, query caching, observability, abuse controls, image deletion, and a search-quality dashboard.

## Evaluation

Create a fixed test set before tuning. Each query should identify one target category and several acceptable listings; include screenshots, camera photos, cluttered rooms, poor lighting, different viewpoints, locally common products, and seller images with backgrounds.

Measure the components separately:

| Component | Metric | Question answered |
|---|---|---|
| Object detection | Precision, recall, IoU | Did it find the correct product region? |
| Retrieval | Recall@K | Was an acceptable product present in the first K results? |
| Ranking | Precision@K, MRR, NDCG | How highly were the useful matches ranked? |
| System | p50/p95 latency | Is the interaction fast enough? |
| Product behavior | Click/save/contact rate | Do users find the results useful? |

Keep a baseline using the uncropped whole image. The multi-object pipeline should demonstrably beat it on cluttered-photo queries; otherwise, the added complexity has not earned its place.

## Common mistakes

- **Training immediately:** pretrained models are sufficient to build and evaluate the first version.
- **Using only SAM:** segmentation gives masks but does not automatically provide marketplace category semantics; pair it with a detector or user click.
- **Using only an LLM/VLM:** a large multimodal model can describe the product, but catalog retrieval still needs indexed item representations and deterministic filters.
- **Embedding only titles:** user photos and seller wording may differ; store image embeddings and optionally text embeddings.
- **Searching every category:** first predict or select a category, then search that partition to reduce false matches.
- **Evaluating by attractive screenshots:** use labeled queries and retrieval metrics.
- **Ignoring source permission:** build the portfolio demo with authorized or self-created data, link to sources, minimize personal data, and document the data policy.

## Portfolio presentation

Present the project as **“JijiLens: Multi-Object Visual Marketplace Search for Uganda.”** The demo should show: upload a room/outfit photo, animated object boxes, tap one product, visually related Ugandan listings, category/location/price filters, and an admin page showing ingestion and evaluation metrics.

The README should include the architecture, model cards and licenses, data provenance, evaluation method, example failure cases, API contract, Docker startup commands, and a short explanation of how feedback could later improve ranking. That makes the project demonstrate computer vision, vector databases, backend/API engineering, frontend interaction design, MLOps, and locally relevant product thinking rather than appearing to be only a model demo.

## Recommended first implementation

Use this smallest credible version:

```text
Next.js UI
   -> FastAPI
      -> Grounding DINO (object boxes)
      -> OpenCLIP/SigLIP (crop embedding)
      -> PostgreSQL + pgvector HNSW
      -> listings + image URLs
```

Implement manual cropping first, add automatic boxes second, and add SAM 2 masks only after retrieval works. That sequence isolates failures: if search is bad, the embedding/index is the problem; if manual crops work but automatic selection fails, detection is the problem; if candidates are good but ordering is poor, re-ranking is the problem.

---

## References

1. [Learning Transferable Visual Models From Natural Language ...](https://cdn.openai.com/papers/Learning_Transferable_Visual_Models_From_Natural_Language.pdf) - While standard image models jointly train an image feature extractor and a linear classifier to pred...

2. [Update: Expanding access to Meta Segment Anything 2.1 on ...](https://ai.meta.com/blog/segment-anything-2/) - A lightweight mask decoder takes an image embedding for the current frame and encoded prompts to out...

3. [Marrying DINO with Grounded Pre-Training for Open-Set ...](https://arxiv.org/html/2303.05499) - We release some checkpoints and inference codes at https://github.com/IDEA-Research/GroundingDINO.

4. [[PDF] CLIP Under the Microscope: A Fine-Grained Analysis of Multi-Object ...](https://openaccess.thecvf.com/content/CVPR2025/papers/Abbasi_CLIP_Under_the_Microscope_A_Fine-Grained_Analysis_of_Multi-Object_Representation_CVPR_2025_paper.pdf) - CLIP model: the text encoder shows a strong preference for the first mentioned object in textual des...

5. [Visual Image Search Meets E-Commerce at Alibaba](https://102.alibaba.com/detail/?id=171) - Collaborating with the Machine Intelligence Technology Lab, Alibaba developed Pailitao, an applicati...

6. [[2102.04674] Visual Search at Alibaba](https://arxiv.org/abs/2102.04674) - by Y Zhang · 2021 · Cited by 195 — This paper introduces the large scale visual search algorithm and...

7. [Large scale long-tailed product recognition system at Alibaba](https://ar5iv.labs.arxiv.org/html/2102.04652) - In the online process, the core function is a real time product recognition service in charge of pre...

8. [Virtual ID Discovery from E-commerce Media at Alibaba](https://ar5iv.labs.arxiv.org/html/2102.04667) - # Virtual ID Discovery from E-commerce Media at Alibaba
Exploiting Richness of User Click Behavior f...

9. [Vision API Product Search documentation | Google Cloud Documentation](https://docs.cloud.google.com/vision/product-search/docs) - applies machine learning to compare the product in the user's query image with

10. [Searching for Products | Vision API Product Search](https://docs.cloud.google.com/vision/product-search/docs/searching) - You can find similar products to a given image by passing the image's Google Cloud Storage URI, web ...

11. [search.md](https://qdrant.tech/documentation/search/search.md) - * Cosine similarity: `Cosine`  - <https://en.wikipedia.org/wiki/Cosine_similarity>

12. [Search](https://qdrant.tech/documentation/search/search/) - The most typical metric used in similarity learning models is the cosine metric.

13. [pgvector Documentation — Setup, Indexing & Reference](https://pgvector.app/docs/) - It generally gives better query performance than IVFFlat for the same recall, at the cost of slower ...

14. [HNSW](https://docs.pgedge.com/pgvector/v0-8-1/hnsw/) - CREATE INDEX ON items USING hnsw (embedding vector_cosine_ops);

15. [Grounding DINO - Hugging Face](https://huggingface.co/docs/transformers/main/model_doc/grounding-dino) - Grounding DINO extends a closed-set object detection model with a text encoder, enabling open-set ob...

16. [SAM 2: Segment Anything in Images and Videos - GitHub](https://github.com/Segment-Anything/segment-anything-2) - GitHub - Segment-Anything/segment-anything-2: The repository provides code for running inference wit...

17. [[PDF] Who's in and who's out? A case study of multimodal CLIP-filtering in ...](https://arxiv.org/pdf/2405.08209.pdf) - [99] as a pre-trained model that learns images from natural language text through mapping images and...

18. [FashionCLIP: Connecting Language and Images for Product ...](https://arxiv.org/html/2204.03972v2) - The model is trained on over 700k {<image,text>} pairs from the inventory of Farfetch, one of the la...

19. [Marqo/marqo-fashionCLIP](https://huggingface.co/Marqo/marqo-fashionCLIP) - Marqo-FashionCLIP Model Card. Marqo-FashionCLIP and Marqo-FashionSigLIP outperform the previous stat...

20. [Jiji Scraper - Product & Price Data | Apify · Apify](https://apify.com/piotrv1001/jiji-listings-scraper) - This Actor is the practical alternative: no API key, no approval and no account, and it returns the ...

21. [Jiji Scraper — Nigeria, Kenya, Ghana, Uganda & Tanzania Ads - Apify](https://apify.com/logiover/jiji-africa-scraper) - No. This Actor reads publicly available pages and feeds, so there is nothing to register for and no ...

22. [Virtual ID Discovery from E-commerce Media at Alibaba: Exploiting Richness of User Click Behavior for Visual Search Relevance](https://dl.acm.org/doi/10.1145/3357384.3357800) - Virtual ID Discovery from E-commerce Media at Alibaba: Exploiting Richness of User Click Behavior fo...

