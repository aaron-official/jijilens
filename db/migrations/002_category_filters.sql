-- A collection category is now our own key plus a Jiji category and optional
-- listing filters, so one Jiji category can feed several of ours
-- (furniture -> sofas, beds, tables, chairs).
ALTER TABLE categories RENAME COLUMN slug TO key;
ALTER TABLE categories ADD COLUMN jiji_slug TEXT;
ALTER TABLE categories ADD COLUMN filters JSONB NOT NULL DEFAULT '{}'::jsonb;
UPDATE categories SET jiji_slug = key WHERE jiji_slug IS NULL;
ALTER TABLE categories ALTER COLUMN jiji_slug SET NOT NULL;
