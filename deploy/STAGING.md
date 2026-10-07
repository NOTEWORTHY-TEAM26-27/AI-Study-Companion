# Staging server setup (SCRUM-61)

One Amazon Lightsail server runs the app behind Caddy, which provides HTTPS.
The URL uses sslip.io because the course cannot buy a domain name.

## One-time setup

1. In Lightsail (region us-east-1), create an instance: Linux/Unix, Ubuntu 24.04 LTS, the 2 GB plan.
2. Networking tab: create a static IP and attach it to the instance.
3. Networking tab, IPv4 firewall: allow SSH (22), HTTP (80), and HTTPS (443). Add 443 if it is missing.
4. Connect with the browser SSH button, then install Docker:

   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker ubuntu
   exit
   ```

   Reconnect so the docker group applies.
5. Let the server read the private repository with a read-only deploy key:

   ```bash
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/github_deploy
   cat ~/.ssh/github_deploy.pub
   ```

   Add the printed key on GitHub: repository Settings, Deploy keys, Add deploy key (leave write access off). Then:

   ```bash
   printf 'Host github.com\n  IdentityFile ~/.ssh/github_deploy\n' >> ~/.ssh/config
   git clone git@github.com:NOTEWORTHY-TEAM26-27/AI-Study-Companion.git ~/noteworthy
   ```
6. Create the server's `.env` (never commit it):

   ```bash
   cd ~/noteworthy
   cp deploy/staging.env.example .env
   nano .env
   ```

   Set `STAGING_HOST` to the static IP with dashes plus `.sslip.io`, for example `3-85-12-34.sslip.io`.

## Deploy

```bash
cd ~/noteworthy
./scripts/deploy_staging.sh
```

The script pulls `main`, rebuilds, restarts, and waits until `https://<STAGING_HOST>/health` answers.
The first run takes a minute longer while Caddy gets the certificate.

## Useful commands

```bash
docker compose -f compose.yaml -f compose.staging.yaml ps
docker compose -f compose.yaml -f compose.staging.yaml logs --tail=100 app
docker compose -f compose.yaml -f compose.staging.yaml down
```

Uploaded documents live in the `documents` Docker volume and survive redeploys.

## Not yet on staging

- Model answers: the app calls Ollama until SCRUM-60 switches it to Bedrock. Upload and quiz work without it.
- Deploy on merge is SCRUM-79 (Sprint 3); for now run the script by hand after merging to `main`.
