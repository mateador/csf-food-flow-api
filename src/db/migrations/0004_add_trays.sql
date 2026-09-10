-- Migration 0004: trays and the gross/net weight split.
--
-- weight_kg is renamed to gross_weight_kg -- it always represented what
-- the scale reads (food + trays together); the name just didn't say so
-- explicitly. A rename preserves existing data, no data loss needed here.
--
-- net_weight_kg is a new NOT NULL column. Existing rows have no recorded
-- tray usage (trays didn't exist as a concept before this migration), so
-- they're backfilled with net_weight_kg = gross_weight_kg -- i.e. "no
-- trays recorded historically" rather than guessing at a deduction that
-- was never captured. This is a judgement call, not a destructive wipe
-- like migration 0002's -- flagging it explicitly in case a straight
-- data reset is preferred instead for this round of changes.

ALTER TABLE weigh_entries RENAME COLUMN weight_kg TO gross_weight_kg;

ALTER TABLE weigh_entries ADD COLUMN net_weight_kg NUMERIC(10, 2);
UPDATE weigh_entries SET net_weight_kg = gross_weight_kg WHERE net_weight_kg IS NULL;
ALTER TABLE weigh_entries ALTER COLUMN net_weight_kg SET NOT NULL;
ALTER TABLE weigh_entries ADD CONSTRAINT weigh_entries_net_weight_non_negative
    CHECK (net_weight_kg >= 0);

-- ============================================================
-- tray_types -- reference data, same pattern as food_categories.
-- Actual rows inserted below so this migration is self-sufficient on a
-- fresh deploy; seed.py's copy of the same list is for fresh local dev
-- setups and must be kept in sync by hand.
-- ============================================================
CREATE TABLE tray_types (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    weight_kg NUMERIC(10, 2) NOT NULL CHECK (weight_kg > 0),
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO tray_types (code, name, weight_kg) VALUES
    ('MEDIUM', 'Medium', 1.6),
    ('LARGE', 'Large', 1.9),
    ('LARGE_COLLAPSIBLE', 'Large collapsible', 1.8),
    ('HALF_COLLAPSIBLE', 'Half-size collapsible', 0.9),
    ('MEDIUM_COLLAPSIBLE', 'Medium collapsible', 1.6),
    ('LITTLE_COLLAPSIBLE', 'Little collapsible', 1.5),
    ('SMALL_COLLAPSIBLE', 'Small collapsible', 1.3),
    ('HALF_SOLID', 'Half size solid', 1.0)
ON CONFLICT (code) DO NOTHING;

-- ============================================================
-- entry_trays -- which trays, and how many of each, were used on a
-- given weigh entry. One row per (entry, tray type); a repeat of the
-- same type increases quantity rather than creating a second row.
-- ============================================================
CREATE TABLE entry_trays (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id UUID NOT NULL REFERENCES weigh_entries(id) ON DELETE CASCADE,
    tray_type_code TEXT NOT NULL REFERENCES tray_types(code),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT entry_trays_unique_type_per_entry UNIQUE (entry_id, tray_type_code)
);

CREATE INDEX idx_entry_trays_entry_id ON entry_trays (entry_id);
