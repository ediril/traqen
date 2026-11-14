# Schema Changes Migrate The Database In Place

Date: 2026-10-25

## Decision

A schema change never costs the owner their data, and they are never asked to
delete `traqen.db`. With each change:

1. Back up `traqen.db` as a timestamped copy next to it
   (`traqen.db.bak-YYYYMMDD-HHMMSS`).
2. Update it in place to the new schema in one transaction, carrying the
   existing data over to the new model.
3. Bump `SCHEMA_VERSION` in `db.py`.
4. Check that the result matches a freshly created database.

There is no migration framework: each change is a one-off script run once
against the owner's database.

## Why

The database holds the owner's real traqs. With one SQLite file and one user, a
hand-written transaction per change carries the data over without the machinery
of a migration framework (owner directive).

## Mechanism

`db.py` stores `SCHEMA_VERSION` in `PRAGMA user_version` and refuses to open a
database with tables at another version, so a schema change that wasn't
migrated fails at startup instead of running against the wrong tables.
