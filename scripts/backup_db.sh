#!/usr/bin/env bash
# Back up the staging database (SCRUM-225): accounts, sessions, quizzes and attempts.
# Run on the staging Droplet as deploy from the repo folder. deploy_staging.sh also installs a
# cron job that runs it every night. Saves ~/backups/noteworthy-db-<UTC time>.sql.gz and keeps
# the newest 7. Uploaded PDFs are files, backed up by backup_staging.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "Missing .env. Run this on the staging server (see deploy/STAGING.md)." >&2
  exit 1
fi

compose=(docker compose -f compose.yaml -f compose.staging.yaml)
backup_dir="$HOME/backups"
keep=7
archive="$backup_dir/noteworthy-db-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
partial="$archive.partial"

if ! "${compose[@]}" exec -T db pg_isready -U noteworthy -d noteworthy >/dev/null; then
  echo "The database container is not running or not ready, so nothing was backed up." >&2
  echo "See: ${compose[*]} ps" >&2
  exit 1
fi

# Backups hold password hashes and student scores, so only the deploy user can read them.
umask 077
mkdir -p "$backup_dir"
if [ -e "$archive" ] || [ -e "$partial" ]; then
  echo "$archive already exists. Wait a second and run again." >&2
  exit 1
fi
# If anything below fails, remove only this run's unfinished file.
trap 'rm -f "$partial"' EXIT

# pg_dump runs inside the db container over its local socket, so no password is needed.
# --clean makes the restore replace the tables that are there (see deploy/STAGING.md).
if ! "${compose[@]}" exec -T db pg_dump -U noteworthy -d noteworthy --clean --if-exists \
  | gzip >"$partial"; then
  echo "Database backup failed: pg_dump or gzip stopped with an error." >&2
  exit 1
fi
# pg_dump ends a finished dump with this comment.
if ! gzip -t "$partial" || ! ending=$(zcat "$partial" | tail -n 10) \
  || [[ $ending != *"-- PostgreSQL database dump complete"* ]]; then
  echo "The new backup is incomplete, so it was removed. Run the script again." >&2
  exit 1
fi

mv "$partial" "$archive"
trap - EXIT
echo "$(date -u +%FT%TZ) Saved $archive ($(du -h "$archive" | cut -f1))"

# The UTC time in each name sorts oldest first, so all but the last $keep are removed.
shopt -s nullglob
backups=("$backup_dir"/noteworthy-db-*.sql.gz)
for ((i = 0; i < ${#backups[@]} - keep; i++)); do
  rm -f -- "${backups[i]}"
  echo "Removed old backup ${backups[i]}"
done
