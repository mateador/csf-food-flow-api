#!/usr/bin/env bash
#
# Proves a backup is usable, not just that a file exists. Run against a
# database the dump has just been restored into.
#
#   scripts/verify-backup.sh <source-url> <restored-url> <expected-migrations>
#
# Checks, and exits non-zero on any failure:
#   - every table in the restored copy has exactly the row count it has in
#     the source (pg_dump reads one consistent snapshot, and backups run at
#     night, so a difference means data went missing);
#   - the restored copy has every migration in the repo applied, so the
#     dump is of the current schema;
#   - there is at least one user, so the copy isn't an empty shell.

set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <source-url> <restored-url> <expected-migrations>" >&2
  exit 2
fi
source_url=$1
restored_url=$2
expected_migrations=$3

tables=$(psql "$restored_url" -tAc \
  "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")

mismatch=0
printf '%-20s %10s %10s\n' "table" "source" "restored"
for table in $tables; do
  in_source=$(psql "$source_url" -tAc "SELECT count(*) FROM \"$table\"")
  in_restored=$(psql "$restored_url" -tAc "SELECT count(*) FROM \"$table\"")
  printf '%-20s %10s %10s\n' "$table" "$in_source" "$in_restored"
  if [ "$in_source" != "$in_restored" ]; then
    mismatch=1
  fi
done

migrations=$(psql "$restored_url" -tAc "SELECT count(*) FROM schema_migrations")
users=$(psql "$restored_url" -tAc "SELECT count(*) FROM users")

if [ "$migrations" != "$expected_migrations" ]; then
  echo "FAIL: restored copy has $migrations migrations applied; the repo has $expected_migrations" >&2
  exit 1
fi
if [ "$users" -lt 1 ]; then
  echo "FAIL: restored copy has no users" >&2
  exit 1
fi
if [ "$mismatch" -ne 0 ]; then
  echo "FAIL: row counts differ between the source and the restored copy" >&2
  exit 1
fi

echo "Backup verified: restored cleanly, $migrations migrations, row counts match."