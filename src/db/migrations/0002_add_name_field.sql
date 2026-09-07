-- Migration 0002: add a mandatory `name` field to weigh_entries.
--
-- Per explicit instruction, this clears existing weigh_entries (and their
-- audit_log records, which reference entries that are about to stop
-- existing) BEFORE adding the new NOT NULL column. This is a deliberate
-- destructive step, not a bug -- it avoids needing a DEFAULT value or a
-- backfill for existing rows, on the understanding that current data in
-- this table does not need to be preserved.
--
-- DO NOT reuse this migration as a template for a schema change against a
-- database with real data you need to keep. A NOT NULL column addition
-- against existing rows normally needs a DEFAULT value or a backfill
-- step instead of a TRUNCATE -- this file is intentionally the
-- exception, not the pattern.

TRUNCATE TABLE audit_log, weigh_entries;

ALTER TABLE weigh_entries ADD COLUMN name TEXT NOT NULL;
