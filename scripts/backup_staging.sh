#!/usr/bin/env bash
# Back up staging's saved files (SCRUM-105). Run on the staging Droplet as deploy from the repo folder.
# Copies /data/documents (uploaded PDFs, issued quizzes, saved attempts) from the running app container
# to ~/backups. It only reads the volume and never changes or deletes old backups.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "Missing .env. Run this in the staging repo folder (see deploy/STAGING.md)." >&2
  exit 1
fi

compose=(docker compose -f compose.yaml -f compose.staging.yaml)
backup_dir="$HOME/backups"
archive="$backup_dir/noteworthy-documents-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
partial="$archive.partial"

if ! "${compose[@]}" exec -T app true; then
  echo "The app container is not running, so there is nothing to back up. See: ${compose[*]} ps" >&2
  exit 1
fi

# Backups hold student files, so only the deploy user can read them.
umask 077
mkdir -p "$backup_dir"
if [ -e "$archive" ] || [ -e "$partial" ]; then
  echo "$archive already exists. Wait a second and run again." >&2
  exit 1
fi
# If anything below fails, remove only this run's unfinished file.
trap 'rm -f "$partial"' EXIT

echo "Backing up /data/documents to $archive"
# .upload-* files are unfinished uploads that the app renames or removes, so leave them out.
if ! "${compose[@]}" exec -T app tar -C /data/documents --exclude='.upload-*' -cf - . \
  | gzip >"$partial"; then
  echo "Backup failed. If an upload happened during the backup, run the script again." >&2
  exit 1
fi

# Read the whole archive back and count what it holds. A document counts only when both its .json
# and .pdf are there, the same rule the app uses at startup.
if ! gzip -t "$partial" || ! counts=$(tar -tzf "$partial" | awk '
  { entries++ }
  /^\.\/[^\/]+\.pdf$/ { pdf[substr($0, 1, length($0) - 4)] = 1 }
  /^\.\/[^\/]+\.json$/ { meta[substr($0, 1, length($0) - 5)] = 1 }
  /^\.\/quizzes\/[^\/]+\.json$/ { quizzes++ }
  /^\.\/attempts\/[^\/]+\/[^\/]+\.json$/ { attempts++ }
  END {
    for (name in meta) if (name in pdf) documents++
    print entries + 0, documents + 0, quizzes + 0, attempts + 0
  }'); then
  echo "The new archive did not list cleanly, so it was removed. Run the script again." >&2
  exit 1
fi
read -r entries documents quizzes attempts <<<"$counts"
if [ "$entries" -eq 0 ]; then
  echo "The new archive is empty, so it was removed. Check: ${compose[*]} logs --tail=100 app" >&2
  exit 1
fi

mv "$partial" "$archive"
trap - EXIT

echo "Saved $archive ($(du -h "$archive" | cut -f1))"
echo "It holds $documents documents, $quizzes issued quizzes, and $attempts saved attempts."

shopt -s nullglob
backups=("$backup_dir"/noteworthy-documents-*.tar.gz)
echo "Backups in $backup_dir: ${#backups[@]}, $(du -ch "${backups[@]}" | tail -n 1 | cut -f1) in total."

host=$(grep -E '^STAGING_HOST=' .env | cut -d= -f2- | tr -d '"[:space:]' || true)
echo "Copy it off the server. On your own computer, run:"
echo "  scp deploy@${host:-<STAGING_HOST>}:backups/$(basename "$archive") ."
