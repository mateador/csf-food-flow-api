-- Migration 0008: add an "Other" source location, a catch-all for a
-- Food In/Out "From" that doesn't fit the named list migration 0007 seeded.

INSERT INTO source_locations (name) VALUES ('Other')
ON CONFLICT (name) DO NOTHING;
