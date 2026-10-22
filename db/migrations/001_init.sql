-- JijiLens catalog: listings and their images, plus the state of collecting them.
-- Only non-personal listing data lives here: no seller names, ids, phone
-- numbers, addresses or chat links.

-- Enabled now so milestone 1 only has to add embedding columns. Their
-- dimension depends on the model, so they are not created yet.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE categories (
  id      BIGSERIAL PRIMARY KEY,
  slug    TEXT UNIQUE NOT NULL,   -- jiji.ug/<slug>, as in config/collect.toml
  label   TEXT NOT NULL
);

CREATE TABLE listings (
  id                      BIGSERIAL PRIMARY KEY,
  source_id               TEXT UNIQUE NOT NULL,   -- Jiji's advert guid
  jiji_id                 BIGINT,
  collection_category_id  BIGINT NOT NULL REFERENCES categories(id),
  jiji_category_slug      TEXT,                   -- the ad's own (sub)category
  jiji_category_name      TEXT,
  title                   TEXT NOT NULL,
  description             TEXT,                   -- contact details scrubbed
  attributes              JSONB NOT NULL DEFAULT '{}'::jsonb,
  price_ugx               BIGINT,
  price_text              TEXT,
  region                  TEXT,                   -- coarse: division or district
  region_slug             TEXT,
  source_url              TEXT NOT NULL,          -- canonical ad URL, no query string
  is_active               BOOLEAN NOT NULL DEFAULT TRUE,
  posted_at               TIMESTAMPTZ,
  edited_at               TIMESTAMPTZ,
  status                  TEXT NOT NULL DEFAULT 'discovered'
                          CHECK (status IN ('discovered', 'detailed', 'failed', 'gone')),
  error                   TEXT,
  first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX listings_status_idx ON listings (status);
CREATE INDEX listings_category_idx ON listings (collection_category_id);

CREATE TABLE listing_images (
  id               BIGSERIAL PRIMARY KEY,
  listing_id       BIGINT NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
  source_image_id  BIGINT NOT NULL,   -- Jiji's image id
  position         INT NOT NULL,      -- 0 is the main image
  is_main          BOOLEAN NOT NULL DEFAULT FALSE,
  url              TEXT NOT NULL,
  width            INT,
  height           INT,
  local_path       TEXT,              -- relative to DATA_DIR
  sha256           TEXT,
  phash            TEXT,              -- perceptual hash, for near-duplicate analysis
  bytes            INT,
  status           TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'ok', 'rejected', 'failed')),
  reject_reason    TEXT,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (listing_id, source_image_id)
);

CREATE INDEX listing_images_status_idx ON listing_images (status);
CREATE INDEX listing_images_sha256_idx ON listing_images (sha256);
