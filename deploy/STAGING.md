# Staging server setup (SCRUM-61)

One DigitalOcean Droplet runs the app behind Caddy, which provides HTTPS.
The URL uses sslip.io because the course cannot buy a domain name.

## One-time setup

1. In the DigitalOcean team, create a Droplet: Ubuntu 24.04 LTS, Basic, Regular, 2 GB / 1 vCPU,
   a US region such as NYC3. Add your SSH key (or use a password and the browser console).
2. Networking, Reserved IPs: assign a Reserved IP to the Droplet so the URL stays the same if the Droplet
   is ever replaced. It is free while assigned and $5 per month if left unassigned.
3. Networking, Firewalls: create a Cloud Firewall that allows inbound SSH (22), HTTP (80), and HTTPS (443),
   and apply it to the Droplet.
4. Open the Droplet's Web Console (or `ssh root@<reserved-ip>`; the login user is root), then create a
   deploy user and install Docker:

   ```bash
   adduser --disabled-password --gecos "" deploy
   usermod -aG sudo deploy
   curl -fsSL https://get.docker.com | sh
   usermod -aG docker deploy
   su - deploy
   ```

   Run the rest as the `deploy` user.
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

   Set `STAGING_HOST` to the Reserved IP with dashes plus `.sslip.io`, for example `203-0-113-10.sslip.io`.
   Paste the DigitalOcean model access key after `DIGITALOCEAN_API_KEY=`. After changing `.env`, run the
   deploy script so the app picks it up.

## Deploy

```bash
cd ~/noteworthy
./scripts/deploy_staging.sh
```

The script pulls `main`, rebuilds, restarts, and waits until `https://<STAGING_HOST>/health` answers.
The first run takes a minute longer while Caddy gets the certificate.

## Deploy on merge (SCRUM-79)

`.github/workflows/deploy-staging.yml` deploys staging after CI passes on a push to `main`.
It can also be run by hand from the Actions tab (Deploy to staging, Run workflow). It connects
as `deploy` with its own SSH key, and the server only lets that key run the deploy script.

One-time setup, on your own computer (needs SSH access to the server and the GitHub CLI):

```bash
./scripts/setup_deploy_key.sh
```

It makes a new SSH key, lets that key run only `scripts/deploy_staging.sh` on the server, and
saves the private key as the `STAGING_SSH_KEY` repository secret. No copy stays on your computer.

To stop automatic deploys, delete the `STAGING_SSH_KEY` secret or remove that line from
`~/.ssh/authorized_keys` on the server. Running the script by hand still works.

## Useful commands

```bash
docker compose -f compose.yaml -f compose.staging.yaml ps
docker compose -f compose.yaml -f compose.staging.yaml logs --tail=100 app
docker compose -f compose.yaml -f compose.staging.yaml down
```

Uploaded documents live in the `documents` Docker volume and survive redeploys.

## Model answers on staging

Staging uses DigitalOcean serverless inference (`AI_PROVIDER=digitalocean`, model `llama-4-maverick` by
default). Each answer uses prepaid credit. If `DIGITALOCEAN_API_KEY` is empty, answers return 503 and
upload and quiz still work.
