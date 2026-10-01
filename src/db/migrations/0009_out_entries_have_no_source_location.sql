-- Migration 0009: OUT entries must never have a source_location_id.
--
-- source_location_id ("From") only means something for food arriving
-- (IN) -- food leaving the centre (OUT) is being redistributed, not
-- sourced from a donor/shop. Enforced in application code (see
-- src/modules/entries/routes.py) for a clear error message; this is the
-- DB-level backstop, same principle as weigh_entries_in_no_destination in
-- migration 0001.
--
-- Safe to add without a backfill: every existing OUT row already has
-- source_location_id IS NULL (the column didn't exist until migration
-- 0007, and no code path has ever set it on an OUT entry), so this adds
-- zero conflict with current data.
ALTER TABLE weigh_entries
    ADD CONSTRAINT weigh_entries_out_no_source_location
    CHECK (entry_type != 'OUT' OR source_location_id IS NULL);
