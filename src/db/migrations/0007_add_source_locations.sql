-- Migration 0007: source locations ("From" on Food In / Food Out).
--
-- Where surplus food came from before arriving at a hub or food centre.
-- Modeled the same way as `locations` (admin-manageable table, not a fixed
-- enum) since new donors/shops will be added over time -- see
-- src/modules/source_locations/routes.py.

CREATE TABLE source_locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT source_locations_name_unique UNIQUE (name)
);

CREATE INDEX idx_source_locations_active ON source_locations (active) WHERE active = true;

INSERT INTO source_locations (name) VALUES
    ('Maison Clement'),
    ('Co-op'),
    ('Aldi'),
    ('Lidl'),
    ('Salvation Army'),
    ('Tesco'),
    ('Waitrose'),
    ('Mayfield Produce'),
    ('Sainsbury'),
    ('Fareshare (free)')
ON CONFLICT (name) DO NOTHING;

-- Nullable, unlike locations.id on weigh_entries: existing rows predate this
-- field and have no source recorded, and per explicit instruction they are
-- left alone rather than backfilled with a fabricated value (see migration
-- 0002 for why a TRUNCATE/backfill is not the default move here either).
-- New entries are required to supply source_location_id -- enforced in
-- application code (src/modules/entries/routes.py), not a DB NOT NULL.
ALTER TABLE weigh_entries ADD COLUMN source_location_id UUID REFERENCES source_locations(id);
CREATE INDEX idx_weigh_entries_source_location_id ON weigh_entries (source_location_id);
