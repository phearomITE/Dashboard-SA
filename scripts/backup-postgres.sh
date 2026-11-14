#!/usr/bin/env bash
set -euo pipefail
umask 077

mkdir -p /backups
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="/backups/sa-alert-${stamp}.dump"
temporary="${target}.partial"
restore_db="restore_check_$(date -u +%s)_$$"

cleanup() {
  rm -f -- "$temporary"
  dropdb --if-exists "$restore_db" >/dev/null 2>&1 || true
}
trap cleanup EXIT

pg_dump --format=custom --compress=6 --file="$temporary" "$PGDATABASE"
pg_restore --list "$temporary" >/dev/null
createdb "$restore_db"
pg_restore --exit-on-error --no-owner --no-acl --dbname="$restore_db" "$temporary" >/dev/null
dropdb "$restore_db"
mv -- "$temporary" "$target"
find /backups -maxdepth 1 -type f -name 'sa-alert-*.dump' -mtime "+${BACKUP_RETENTION_DAYS:-14}" -delete
echo "Backup and restore check passed: $target"
