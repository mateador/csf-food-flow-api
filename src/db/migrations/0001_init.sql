-- Migration 0001: initial schema
-- Applied via db/migrate.py, tracked in schema_migrations.

CREATE EXTENSION IF NOT EXISTS pgcrypto; -- for gen_random_uuid()

-- ============================================================
-- locations
-- ============================================================
CREATE TABLE locations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('HUB', 'FOOD_CENTRE')),
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT locations_name_unique UNIQUE (name)
);

CREATE INDEX idx_locations_type ON locations (type);
CREATE INDEX idx_locations_active ON locations (active) WHERE active = true;

-- ============================================================
-- users
-- ============================================================
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('ADMIN', 'FOOD_CENTRE', 'HUB')),
    location_id UUID REFERENCES locations(id),
    active BOOLEAN NOT NULL DEFAULT true,
    last_login_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT users_email_unique UNIQUE (email),
    -- location_id is required when role = HUB
    CONSTRAINT users_hub_requires_location
        CHECK (role != 'HUB' OR location_id IS NOT NULL)
);

-- email stored lowercase by convention; enforced in application code at
-- write time (Postgres CHECK against lower(email) = email would also work,
-- but normalizing on write is simpler to reason about with msgspec).
CREATE UNIQUE INDEX idx_users_email ON users (email);
CREATE INDEX idx_users_role ON users (role);
CREATE INDEX idx_users_location_id ON users (location_id);
CREATE INDEX idx_users_active ON users (active) WHERE active = true;

-- ============================================================
-- food_categories
-- ============================================================
CREATE TABLE food_categories (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- weigh_entries
-- ============================================================
CREATE TABLE weigh_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_uuid UUID,
    entry_type TEXT NOT NULL CHECK (entry_type IN ('IN', 'OUT')),
    location_id UUID NOT NULL REFERENCES locations(id),
    destination_location_id UUID REFERENCES locations(id),
    food_category_code TEXT NOT NULL REFERENCES food_categories(code),
    weight_kg NUMERIC(10, 2) NOT NULL CHECK (weight_kg > 0),
    collection_date DATE NOT NULL,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'VOID')),
    void_reason TEXT,
    created_by UUID NOT NULL REFERENCES users(id),
    updated_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- IN entries must not have a destination; OUT entries must have one,
    -- and it must not equal the source location. Enforced at the DB layer
    -- (not just application code) because this is a correctness invariant
    -- the data must never violate, regardless of which code path writes it.
    CONSTRAINT weigh_entries_in_no_destination
        CHECK (entry_type != 'IN' OR destination_location_id IS NULL),
    CONSTRAINT weigh_entries_out_requires_destination
        CHECK (entry_type != 'OUT' OR destination_location_id IS NOT NULL),
    CONSTRAINT weigh_entries_destination_not_self
        CHECK (destination_location_id IS NULL OR destination_location_id != location_id)
);

CREATE UNIQUE INDEX idx_weigh_entries_client_uuid
    ON weigh_entries (client_uuid) WHERE client_uuid IS NOT NULL;
CREATE INDEX idx_weigh_entries_collection_date ON weigh_entries (collection_date);
CREATE INDEX idx_weigh_entries_location_id ON weigh_entries (location_id);
CREATE INDEX idx_weigh_entries_destination_location_id ON weigh_entries (destination_location_id);
CREATE INDEX idx_weigh_entries_food_category_code ON weigh_entries (food_category_code);
CREATE INDEX idx_weigh_entries_entry_type ON weigh_entries (entry_type);
CREATE INDEX idx_weigh_entries_status ON weigh_entries (status);
CREATE INDEX idx_weigh_entries_reporting
    ON weigh_entries (collection_date, location_id, entry_type, food_category_code);
CREATE INDEX idx_weigh_entries_created
    ON weigh_entries (created_by, created_at);

-- ============================================================
-- audit_log
-- ============================================================
CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type TEXT NOT NULL DEFAULT 'WEIGH_ENTRY',
    entity_id UUID NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id),
    action TEXT NOT NULL CHECK (action IN ('CREATE', 'UPDATE', 'VOID')),
    before JSONB,
    after JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_log_entity_id ON audit_log (entity_id);
CREATE INDEX idx_audit_log_user_id ON audit_log (user_id);
CREATE INDEX idx_audit_log_created_at ON audit_log (created_at);

-- ============================================================
-- magic_link_tokens
-- ============================================================
CREATE TABLE magic_link_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT magic_link_tokens_hash_unique UNIQUE (token_hash)
);

CREATE UNIQUE INDEX idx_magic_link_tokens_hash ON magic_link_tokens (token_hash);
CREATE INDEX idx_magic_link_tokens_user_id ON magic_link_tokens (user_id);
CREATE INDEX idx_magic_link_tokens_expires_at ON magic_link_tokens (expires_at);
CREATE INDEX idx_magic_link_tokens_active ON magic_link_tokens (used_at) WHERE used_at IS NULL;
