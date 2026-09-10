-- Migration 0003: add three new food categories, alongside the existing
-- FRESH / FROZEN / AMBIENT (not replacing them). ON CONFLICT DO NOTHING
-- makes this safe to run even if these codes somehow already exist.

INSERT INTO food_categories (code, name) VALUES
    ('VEG_FRUIT', 'Veg/Fruit'),
    ('OTHER_FRESH', 'Other fresh (NOT Veg/Fruit)'),
    ('BAKERY', 'Bakery')
ON CONFLICT (code) DO NOTHING;
