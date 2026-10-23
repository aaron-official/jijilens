"""How well does search work? Measured automatically, before any UI exists.

Leave-one-out retrieval: for every listing with two or more photos, search
with its second photo against the whole catalog (minus that photo) and check
where the listing itself ranks. Recall@K is the share of queries whose listing
comes back in the top K. It is a lower bound on real quality: another seller's
ad for the same product counts as a miss here, though a user would be happy
with it.

Two more measurements come out of the same run:
- Similarity calibration. A query's best match in a *different* category
  stands in for searching something the catalog doesn't stock, so its spread
  tells us where a "no close matches" threshold should sit.
- HNSW vs exact. A sample of queries goes through pgvector's approximate index
  and through brute force in numpy; the overlap of their top K is the index's
  recall, and both are timed.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

import numpy as np
import psycopg

from jijilens.search import store
from jijilens.search.query import IMAGES_PER_RESULT, rank_listings

KS = (1, 5, 10)


@dataclass(frozen=True)
class Catalog:
    image_ids: np.ndarray  # (n,)
    listing_ids: np.ndarray  # (n,)
    positions: np.ndarray  # (n,)
    categories: np.ndarray  # (n,) str
    vectors: np.ndarray  # (n, dim), L2-normalised float32

    @classmethod
    def from_rows(cls, rows: list[dict]) -> "Catalog":
        return cls(
            image_ids=np.array([r["image_id"] for r in rows], dtype=np.int64),
            listing_ids=np.array([r["listing_id"] for r in rows], dtype=np.int64),
            positions=np.array([r["position"] for r in rows], dtype=np.int64),
            categories=np.array([r["category"] for r in rows]),
            vectors=np.vstack([_array(r["embedding"]) for r in rows]),
        )


def _array(value) -> np.ndarray:
    """pgvector returns an unsized `vector` column as a Vector object, not a numpy array."""
    return np.asarray(value.to_numpy() if hasattr(value, "to_numpy") else value, dtype=np.float32)


def query_rows(catalog: Catalog) -> np.ndarray:
    """Row of each multi-photo listing's second photo (by position)."""
    order = np.lexsort((catalog.positions, catalog.listing_ids))
    rows = []
    for i in range(1, len(order)):
        same = catalog.listing_ids[order[i]] == catalog.listing_ids[order[i - 1]]
        first_of_previous = i < 2 or catalog.listing_ids[order[i - 2]] != catalog.listing_ids[order[i]]
        if same and first_of_previous:
            rows.append(order[i])
    return np.array(sorted(rows), dtype=np.int64)


def exact_results(catalog: Catalog, rows: np.ndarray, k: int, chunk: int = 256) -> list[dict]:
    """Brute-force search for each query row. One record per query."""
    n_images = min(k * IMAGES_PER_RESULT, len(catalog.image_ids) - 1)
    records = []
    for start in range(0, len(rows), chunk):
        batch = rows[start : start + chunk]
        sims = catalog.vectors[batch] @ catalog.vectors.T
        sims[np.arange(len(batch)), batch] = -np.inf  # never match the query photo itself
        top = np.argpartition(-sims, n_images - 1, axis=1)[:, :n_images]
        for qi, row in enumerate(batch):
            hits = [
                {
                    "listing_id": int(catalog.listing_ids[j]),
                    "similarity": float(sims[qi, j]),
                    "image_id": int(catalog.image_ids[j]),
                    "local_path": "",
                    "category": str(catalog.categories[j]),
                }
                for j in top[qi]
            ]
            ranked = rank_listings(hits, k)
            listing = int(catalog.listing_ids[row])
            category = str(catalog.categories[row])
            own = catalog.listing_ids == listing
            own[row] = False
            other_category = catalog.categories != category
            records.append(
                {
                    "image_id": int(catalog.image_ids[row]),
                    "listing_id": listing,
                    "category": category,
                    "rank": next((n for n, m in enumerate(ranked, 1) if m.listing_id == listing), None),
                    "top1_category": ranked[0].category if ranked else None,
                    "top_listings": [m.listing_id for m in ranked],
                    "own_similarity": float(sims[qi, own].max()),
                    "other_category_similarity": float(sims[qi, other_category].max()) if other_category.any() else None,
                }
            )
    return records


def summarize(records: list[dict], ks: tuple[int, ...] = KS) -> dict:
    def block(rs: list[dict]) -> dict:
        n = len(rs)
        out = {"queries": n}
        for k in ks:
            out[f"recall@{k}"] = sum(1 for r in rs if r["rank"] is not None and r["rank"] <= k) / n if n else 0.0
        out["category@1"] = sum(1 for r in rs if r["top1_category"] == r["category"]) / n if n else 0.0
        return out

    by_category: dict[str, list[dict]] = {}
    for record in records:
        by_category.setdefault(record["category"], []).append(record)
    return {"overall": block(records), "categories": {c: block(rs) for c, rs in sorted(by_category.items())}}


def calibration(records: list[dict]) -> dict:
    """Spread of similarities: to the query's own listing, and to its best match in another category."""
    pct = (10, 25, 50, 75, 90)

    def spread(values: list[float]) -> dict:
        return {f"p{p}": round(float(np.percentile(values, p)), 4) for p in pct} if values else {}

    return {
        "own_listing": spread([r["own_similarity"] for r in records]),
        "other_category": spread([r["other_category_similarity"] for r in records if r["other_category_similarity"] is not None]),
    }


def hnsw_check(
    conn: psycopg.Connection,
    catalog: Catalog,
    model_id: int,
    dim: int,
    records: list[dict],
    *,
    sample: int,
    k: int,
    seed: int = 0,
) -> dict:
    """Run a sample of queries through pgvector's HNSW index and compare with exact search."""
    chosen = random.Random(seed).sample(records, min(sample, len(records)))
    row_of = {int(image_id): i for i, image_id in enumerate(catalog.image_ids)}
    overlaps, hnsw_ms, exact_ms = [], [], []
    for record in chosen:
        vector = catalog.vectors[row_of[record["image_id"]]]

        started = time.perf_counter()
        hits = store.nearest_images(
            conn, model_id, dim, vector, limit=k * IMAGES_PER_RESULT, exclude_image_id=record["image_id"]
        )
        hnsw = [m.listing_id for m in rank_listings(hits, k)]
        hnsw_ms.append((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        sims = catalog.vectors @ vector
        np.argpartition(-sims, k * IMAGES_PER_RESULT)[: k * IMAGES_PER_RESULT]
        exact_ms.append((time.perf_counter() - started) * 1000)

        exact = record["top_listings"][:k]
        overlaps.append(len(set(hnsw) & set(exact)) / len(exact) if exact else 1.0)

    def ms(values: list[float]) -> dict:
        return {"p50": round(float(np.percentile(values, 50)), 2), "p95": round(float(np.percentile(values, 95)), 2)}

    return {
        "queries": len(chosen),
        f"recall@{k}_vs_exact": round(float(np.mean(overlaps)), 4) if overlaps else None,
        "hnsw_ms": ms(hnsw_ms) if hnsw_ms else None,
        "exact_numpy_ms": ms(exact_ms) if exact_ms else None,
    }


def evaluate(conn: psycopg.Connection, model_id: int, dim: int, *, hnsw_sample: int = 200) -> dict:
    catalog = Catalog.from_rows(store.all_embeddings(conn, model_id))
    rows = query_rows(catalog)
    k = max(KS)
    records = exact_results(catalog, rows, k)
    return {
        "images": len(catalog.image_ids),
        "listings": int(len(np.unique(catalog.listing_ids))),
        "retrieval": summarize(records),
        "calibration": calibration(records),
        "hnsw": hnsw_check(conn, catalog, model_id, dim, records, sample=hnsw_sample, k=k),
    }
