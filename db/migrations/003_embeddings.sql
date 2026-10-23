-- Image embeddings: one row per image per model, so models can be compared
-- side by side. Models differ in vector size, so the column has no fixed
-- dimension. Each model gets its own HNSW index over embedding::vector(dim),
-- limited to that model's rows; `jijilens embed` creates it (search/store.py).

CREATE TABLE embedding_models (
  id          SERIAL PRIMARY KEY,
  name        TEXT UNIQUE NOT NULL,   -- e.g. siglip2-b16, see search/embedder.py
  dim         INT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE image_embeddings (
  image_id    BIGINT NOT NULL REFERENCES listing_images(id) ON DELETE CASCADE,
  model_id    INT NOT NULL REFERENCES embedding_models(id) ON DELETE CASCADE,
  embedding   vector NOT NULL,        -- L2-normalised, so cosine distance ranks them
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (image_id, model_id)
);
