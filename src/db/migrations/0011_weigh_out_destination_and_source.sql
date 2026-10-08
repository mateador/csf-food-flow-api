-- Migration 0011: Weigh-out's "Destination" and "From" become their own
-- fixed lists, independent of the hub/location model and of Food In's
-- source_locations.
--
-- "Destination" used to mean "which hub" (destination_location_id, a
-- locations FK). Per explicit instruction it now means something else
-- entirely -- an internal program/use (Batch cook, cafe, ZCC event, ...),
-- unrelated to physical hubs. destination_location_id's old "OUT
-- requires one" rule is dropped below; the column itself is left
-- untouched so historical OUT entries keep whatever hub they recorded.
--
-- "From" on Weigh-out is a brand-new, separate concept from Food In's
-- source_locations (shop/donor names) -- reversing the earlier explicit
-- decision (migration 0009) that Weigh-out has no source at all.
-- Different values, different table, coincidentally the same field
-- label on the form.
--
-- out_destination_id/out_source_id are nullable in the DB, same
-- reasoning as source_location_id: required for new OUT entries at the
-- application level (see src/modules/entries/routes.py), not a hard DB
-- requirement, so nothing retroactive breaks. The "must be null for IN"
-- direction IS safe to enforce at the DB level, unlike that requirement
-- -- these are brand-new columns, so every existing row (IN or OUT)
-- already satisfies that trivially.

CREATE TABLE out_destinations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT out_destinations_name_unique UNIQUE (name)
);

CREATE INDEX idx_out_destinations_active ON out_destinations (active) WHERE active = true;

INSERT INTO out_destinations (name) VALUES
    ('Batch cook'),
    ('cafe'),
    ('catering'),
    ('men''s cook'),
    ('Other'),
    ('P''s PM'),
    ('PAYF'),
    ('ZCC event')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE out_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT out_sources_name_unique UNIQUE (name)
);

CREATE INDEX idx_out_sources_active ON out_sources (active) WHERE active = true;

INSERT INTO out_sources (name) VALUES
    ('Fareshare paid'),
    ('farm'),
    ('FS'),
    ('Other'),
    ('surplus'),
    ('surplus, gleaned')
ON CONFLICT (name) DO NOTHING;

ALTER TABLE weigh_entries ADD COLUMN out_destination_id UUID REFERENCES out_destinations(id);
ALTER TABLE weigh_entries ADD COLUMN out_source_id UUID REFERENCES out_sources(id);

CREATE INDEX idx_weigh_entries_out_destination_id ON weigh_entries (out_destination_id);
CREATE INDEX idx_weigh_entries_out_source_id ON weigh_entries (out_source_id);

ALTER TABLE weigh_entries
    ADD CONSTRAINT weigh_entries_in_no_out_destination
    CHECK (entry_type != 'IN' OR out_destination_id IS NULL);
ALTER TABLE weigh_entries
    ADD CONSTRAINT weigh_entries_in_no_out_source
    CHECK (entry_type != 'IN' OR out_source_id IS NULL);

-- destination_location_id ("which hub") is no longer required for OUT.
ALTER TABLE weigh_entries DROP CONSTRAINT weigh_entries_out_requires_destination;

-- name is no longer mandatory for OUT (enforced in application code
-- only now, same pattern as source_location_id) -- still required for IN.
ALTER TABLE weigh_entries ALTER COLUMN name DROP NOT NULL;
