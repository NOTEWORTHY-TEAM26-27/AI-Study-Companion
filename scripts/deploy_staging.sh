#!/usr/bin/env bash
# Deploy the latest main branch to staging. Run on the Lightsail server from the repo folder.
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
