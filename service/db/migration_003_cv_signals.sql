-- Migration 003 — CV-derived signals (parse & discard: no raw CV is ever stored).
-- Only these derived fields persist; the uploaded file is dropped after parsing.
-- Apply:  psql "$DATABASE_URL" -f service/db/migration_003_cv_signals.sql

alter table profiles add column if not exists has_cv           boolean not null default false;
alter table profiles add column if not exists cv_summary       text;      -- short "Detected: ..." line
alter table profiles add column if not exists years_experience int;
