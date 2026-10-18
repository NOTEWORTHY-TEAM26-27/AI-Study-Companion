#!/usr/bin/env bash
# One-time setup for deploy on merge (SCRUM-79). Run on your own computer, not on the server.
# Makes an SSH key that can only run the deploy script on staging and saves it as a GitHub secret.
set -euo pipefail

server=deploy@143.244.212.41
repo=NOTEWORTHY-TEAM26-27/AI-Study-Companion
gh=$(command -v gh || echo "/c/Program Files/GitHub CLI/gh.exe")
dir=$(mktemp -d)
key="$dir/staging_actions_key"
trap 'rm -rf "$dir"' EXIT

ssh-keygen -q -t ed25519 -N "" -C github-actions-staging -f "$key"
pub=$(cat "$key.pub")
ssh "$server" "echo 'command=\"cd /home/deploy/noteworthy && ./scripts/deploy_staging.sh\",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty $pub' >> ~/.ssh/authorized_keys"
"$gh" secret set STAGING_SSH_KEY --repo "$repo" < "$key"
echo "Done. Merges to main now deploy staging after CI passes."
