#!/usr/bin/env bash
# Deploy the latest main branch to staging. Run on the staging Droplet from the repo folder.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "Missing .env. Copy deploy/staging.env.example to .env and set STAGING_HOST." >&2
  exit 1
fi

host=$(grep -E '^STAGING_HOST=' .env | cut -d= -f2- | tr -d '"[:space:]')
if [ -z "$host" ]; then
  echo "STAGING_HOST is empty in .env." >&2
  exit 1
fi

compose=(docker compose -f compose.yaml -f compose.staging.yaml)

git fetch --quiet origin
git checkout --quiet main
git pull --ff-only --quiet origin main

# The database password is made once and kept in .env; it is never printed. The database
# volume keeps the password it was created with, so a new one is made only before the
# volume exists.
if ! grep -Eq "^POSTGRES_PASSWORD=[\"']?[^\"'[:space:]]" .env; then
  if docker volume inspect "$(basename "$PWD")_database" >/dev/null 2>&1; then
    echo "POSTGRES_PASSWORD is missing from .env, but the database already exists." >&2
    echo "Put the old password back in .env, or set a new one (deploy/STAGING.md, Password)." >&2
    exit 1
  fi
  # Made in its own step, so a failed openssl stops the script before .env changes.
  if ! password=$(openssl rand -hex 24) || [ ${#password} -ne 48 ]; then
    echo "Could not make a database password with openssl, so .env was not changed." >&2
    exit 1
  fi
  sed -i '/^POSTGRES_PASSWORD=/d' .env
  # Start a new line if the file does not end with one.
  if [ -s .env ] && [ -n "$(tail -c 1 .env)" ]; then
    echo >>.env
  fi
  printf 'POSTGRES_PASSWORD=%s\n' "$password" >>.env
  unset password
  echo "Added a new POSTGRES_PASSWORD to .env."
fi
# .env holds the database password and the model key, so only deploy can read it.
chmod 600 .env

# Back up the database every night at 08:30 UTC (2:30 or 3:30 a.m. in Texas). Any older
# backup line is replaced, so running this again keeps one entry.
if command -v crontab >/dev/null 2>&1; then
  mkdir -p "$HOME/backups"
  chmod 700 "$HOME/backups"
  printf -v backup_job '30 8 * * * cd %q && ./scripts/backup_db.sh >>%q 2>&1' \
    "$PWD" "$HOME/backups/backup_db.log"
  { crontab -l 2>/dev/null | grep -vF 'scripts/backup_db.sh' || true; echo "$backup_job"; } \
    | crontab -
else
  echo "crontab is not installed, so nightly database backups are not scheduled." >&2
fi

"${compose[@]}" up -d --build --remove-orphans
docker image prune -f >/dev/null
"${compose[@]}" ps

echo "Waiting for https://${host}/health"
for _ in $(seq 1 30); do
  if curl -fsS "https://${host}/health" >/dev/null 2>&1; then
    echo "Staging is up: https://${host}"
    exit 0
  fi
  sleep 5
done

echo "Health check failed. See: ${compose[*]} logs --tail=100" >&2
exit 1
