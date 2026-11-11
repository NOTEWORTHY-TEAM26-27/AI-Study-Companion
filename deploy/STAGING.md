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
   Paste the DigitalOcean model access key after `DIGITALOCEAN_API_KEY=`. Set the staging login (see
   "Staging login" below). After changing `.env`, run the deploy script so the app picks it up.

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

## Backups (SCRUM-105)

There is no database yet. Uploaded PDFs, issued quizzes, and saved quiz attempts are files in the
`documents` volume, at `/data/documents` in the app container. Nothing backs them up automatically.

Take a backup as `deploy` while the app is running:

```bash
cd ~/noteworthy
./scripts/backup_staging.sh
```

The script saves `~/backups/noteworthy-documents-<UTC time>.tar.gz`, checks that the archive reads
back, and prints how many documents, quizzes, and attempts it holds. It only reads the volume and
never deletes old backups. Delete old ones by hand when they are no longer needed.

Copy each backup off the server. On your own computer, run the `scp` line the script prints:

```bash
scp deploy@<STAGING_HOST>:backups/noteworthy-documents-<UTC time>.tar.gz .
```

Before a big change, such as a Droplet resize or a change to how files are saved, you can also take a
snapshot of the whole Droplet: open the Droplet in DigitalOcean, then Backups & Snapshots, Take snapshot.
DigitalOcean recommends turning the Droplet off first for a consistent copy, so staging is down until it
is back on (https://docs.digitalocean.com/products/snapshots/how-to/snapshot-droplets/, checked
November 2, 2026). A snapshot costs $0.06 per GB of snapshot size per month while it is kept
(https://docs.digitalocean.com/products/snapshots/details/pricing/, checked November 2, 2026).
Delete it once the change works.

To restore a backup:

1. Make sure the app is running. On a new Droplet, run `./scripts/deploy_staging.sh` first.
2. Run `./scripts/backup_staging.sh` so the current files are not lost. This also creates `~/backups`.
3. If the backup you need is not on the server, copy it there from your own computer:

   ```bash
   scp noteworthy-documents-<UTC time>.tar.gz deploy@<STAGING_HOST>:backups/
   ```
4. Unpack it into the volume, then restart the app. The app loads documents only when it starts.

   ```bash
   cd ~/noteworthy
   gunzip -c ~/backups/noteworthy-documents-<UTC time>.tar.gz \
     | docker compose -f compose.yaml -f compose.staging.yaml exec -T app tar -C /data/documents -xf -
   docker compose -f compose.yaml -f compose.staging.yaml restart app
   ```
5. Open `https://<STAGING_HOST>/api/documents` and check that it lists the restored documents. The web
   page itself lists only documents uploaded from that browser.

A restore adds the files in the backup and replaces files with the same name. Files saved after the
backup stay.

## Staging login

The app does not separate students yet (accounts come with CR-11 in Senior Design II), so Caddy asks for
one shared login on every page except `/health`. This keeps people without the login from uploading files,
reading documents, or using model credit. Share the login with the team and Dr. Wang only.

1. Pick a password and keep it out of the repo.
2. On the server, make its hash (type the password twice when asked):

   ```bash
   docker run --rm -it caddy:2 caddy hash-password
   ```

3. In `.env`, set `STAGING_USER` and `STAGING_PASSWORD_HASH`. Keep the single quotes around the hash,
   because it contains `$` signs:

   ```
   STAGING_USER=noteworthy
   STAGING_PASSWORD_HASH='$2a$14$...'
   ```

4. Run `./scripts/deploy_staging.sh`. Without both values, `docker compose` stops with an error and staging
   keeps running the previous version.

To change the password, repeat steps 2 to 4.

## Model answers on staging

Staging uses DigitalOcean serverless inference (`AI_PROVIDER=digitalocean`, model `openai-gpt-oss-120b`
by default, with low reasoning effort). Each answer and each quiz is one model call and uses prepaid
credit; a quiz call sends up to eight passages. If `DIGITALOCEAN_API_KEY` is empty, answers return 503
and upload still works.

The model access key must include the model in use, and a key's models cannot be changed after it is
created. Create the key with `openai-gpt-oss-120b`. To use another model later, create a new key that
includes it, put the new key after `DIGITALOCEAN_API_KEY=` in `.env`, set `DIGITALOCEAN_MODEL` to that
model's ID, remove any `DIGITALOCEAN_REASONING_EFFORT` line, and run the deploy script.

Anthropic models, and OpenAI models other than gpt-oss, need account Tier 3 or higher.

A 503 answer names the HTTP status from DigitalOcean. `(HTTP 401)` or `(HTTP 403)` means the key is
wrong, does not include the model, or the account tier does not allow the model. A 503 that says the answer
was cut off means the reply used the whole limit (`DIGITALOCEAN_MAX_TOKENS`, default 4,096; gpt-oss counts
its reasoning in it).

A quiz never returns 503. If its model call fails or no question passes the checks, the quiz has only the
five cloze multiple-choice questions, and the app log has a warning. A PDF with fewer than five full
statements, such as slides that are mostly formulas or diagrams, then shows an error instead. To see it, run
`docker compose -f compose.yaml -f compose.staging.yaml logs app | grep "model questions\|Question model"`.

## Before the Gate Review (11/30)

Do this a few days before 11/30, so there is time to fix problems.

1. Health: `https://<STAGING_HOST>/health` answers `{"status": "ok"}`.
2. One full flow on staging: upload a PDF, ask one question, make a quiz, and submit it. The quiz should
   have true/false, fill-in-the-blank, and short-answer questions; only multiple choice means the model
   call failed (see above). Reload the page
   and check that the PDF is still selected and Scores by topic still shows the result.
3. Backup: run `./scripts/backup_staging.sh` on the server and copy the file off with `scp`.
4. Cost:
   - DigitalOcean Billing page: check this month's usage so far.
   - Model balance: the prepaid Inference and Agents balance must cover the demo. At $0 the model
     stops and answers fail until more is added.
   - Spend alert: Billing, Spend alerts. The $20 monthly alert is still set.
