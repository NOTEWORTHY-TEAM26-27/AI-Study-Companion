# Staging server setup (SCRUM-61)

One DigitalOcean Droplet runs the app and its PostgreSQL database behind Caddy, which
provides HTTPS.
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
   Paste the DigitalOcean model access key after `DIGITALOCEAN_API_KEY=`. Leave `POSTGRES_PASSWORD` out:
   the deploy script adds it (see Database). After changing `.env`, run the deploy script so the app
   picks it up.

## Deploy

```bash
cd ~/noteworthy
./scripts/deploy_staging.sh
```

The script pulls `main`, adds the database password to `.env` if it is not there yet, installs the
nightly database backup, rebuilds, restarts, and waits until `https://<STAGING_HOST>/health` answers.
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
docker compose -f compose.yaml -f compose.staging.yaml logs --tail=100 db
docker compose -f compose.yaml -f compose.staging.yaml exec db psql -U noteworthy -d noteworthy
docker compose -f compose.yaml -f compose.staging.yaml down
```

Uploaded documents live in the `documents` Docker volume, and accounts, quizzes and attempts in the
`database` volume. Both survive redeploys and `down`; `down --volumes` deletes them.

## Backups (SCRUM-105)

Uploaded PDFs and their extracted passages are files in the `documents` volume, at `/data/documents`
in the app container. Nothing backs them up automatically; this section covers them. Accounts,
quizzes and attempts are in the database, which is backed up every night (see Database backups).
Quizzes and attempts saved as files before the database are in the `documents` volume too. Keep
backups private.

Take a backup as `deploy` while the app is running:

```bash
cd ~/noteworthy
./scripts/backup_staging.sh
```

The script saves `~/backups/noteworthy-documents-<UTC time>.tar.gz`, checks that the archive reads
back, and prints how many documents it holds, and how many quiz and attempt files from before the
database. Current quizzes and attempts are only in the database backups. It only reads the volume
and never deletes old backups. Delete old ones by hand when they are no longer needed.

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
5. With student accounts off, open `https://<STAGING_HOST>/api/documents` and check that it lists the
   restored documents. The web page itself lists only documents uploaded from that browser.
   With student accounts on, `/api/documents` needs a login and shows only that student's documents
   (none from before accounts), so it is not a full check. List the files instead:

   ```bash
   docker compose -f compose.yaml -f compose.staging.yaml exec app ls /data/documents
   ```

   Each document is a `.pdf` and a `.json` with the same ID. Accounts are in the database (see
   Restore the database).

A restore adds the files in the backup and replaces files with the same name. Files saved after the
backup stay.

## Database (SCRUM-225)

Accounts, login sessions, issued quizzes and quiz attempts are in PostgreSQL 16, which runs
as the `db` container with its data in the `database` Docker volume. The volume survives
redeploys, so accounts and attempts do too. Uploaded PDFs stay as files in the `documents`
volume (see Backups). The database publishes no port; only the app and
`docker compose exec` can reach it.

The container is limited to 256 MB, with small PostgreSQL memory settings and at most 20
connections (`compose.staging.yaml`). The app opens at most 10 at once. A request waits at
most 5 seconds for one of them, and a query at most 5 seconds for a lock and 10 seconds in
all; past that the request gets a 503 instead of hanging. PostgreSQL also closes a session
that leaves a transaction open and idle for more than a minute, such as a `psql` with a
forgotten `BEGIN`, so its locks cannot block the app for long.

### Password

`scripts/deploy_staging.sh` adds a random `POSTGRES_PASSWORD` to `.env` on the first deploy,
makes `.env` readable only by `deploy`, and never prints the password. PostgreSQL takes the
password only when the `database` volume is created, so:

- Never change or delete the `POSTGRES_PASSWORD` line in `.env`. If it is missing while the
  volume exists, the deploy script stops instead of making a new one.
- To change the password, or to set one again after the line was lost, put the new value in
  `.env` first, then set the same value inside the database (the container's own `psql` needs
  no password), then run the deploy script. `.env` comes first because Compose stops every
  command, `exec` included, while the `POSTGRES_PASSWORD` line is missing; editing `.env`
  does not change the running containers. Use only letters, digits and hyphens, since the app
  puts the password in a URL. `openssl rand -hex 24` prints a new one to copy:

  ```bash
  cd ~/noteworthy
  openssl rand -hex 24
  nano .env
  docker compose -f compose.yaml -f compose.staging.yaml exec db psql -U noteworthy -d noteworthy -c '\password noteworthy'
  ./scripts/deploy_staging.sh
  ```

  In `nano`, set the `POSTGRES_PASSWORD=` line to the new value. `\password` asks for it
  twice; paste the same value.

### First deploy with the database

The first automatic deploy after this change still runs the old deploy script, which does not
add the password, so it stops with `Set POSTGRES_PASSWORD in .env`. Staging keeps running the
old version. Run `./scripts/deploy_staging.sh` by hand once (or run the Deploy to staging
workflow again); the new script adds the password and starts the database.

Quiz attempts saved as files before this change were test data and are not moved into the
database. They stay in the `documents` volume, unused, so the score history starts empty.

### Database backups

The deploy script installs a cron job for `deploy` that runs `scripts/backup_db.sh` every
night at 08:30 UTC. Running the deploy script again replaces the line, so there is only one.
Check it with `crontab -l`. Each run adds a line to `~/backups/backup_db.log`.

The script saves `~/backups/noteworthy-db-<UTC time>.sql.gz` (a `pg_dump`, run inside the
`db` container), checks that it is complete, and deletes all but the newest 7. It stops with
an error, and leaves no partial file, if the database is not running or the dump fails. Run it
by hand before a big change:

```bash
cd ~/noteworthy
./scripts/backup_db.sh
```

The dumps hold password hashes, hashed session tokens and student scores, never plain
passwords or tokens. They are readable only by `deploy`. Copy them off the server now and then:

```bash
scp deploy@<STAGING_HOST>:backups/noteworthy-db-<UTC time>.sql.gz .
```

### Restore the database

A restore replaces the four tables with the backup's contents, so anything saved after the
backup is lost.

1. Copy the dump you will restore out of `~/backups` first. `backup_db.sh`, by hand or from
   cron, keeps only the newest 7 dumps there, so it could delete an older one before you load
   it:

   ```bash
   cp ~/backups/noteworthy-db-<UTC time>.sql.gz ~/restore.sql.gz
   ```

   If the dump is not on the server, copy it from your own computer to the same place
   instead, then run `chmod 600 ~/restore.sql.gz` on the server:

   ```bash
   scp noteworthy-db-<UTC time>.sql.gz deploy@<STAGING_HOST>:restore.sql.gz
   ```
2. Run `./scripts/backup_db.sh` so the current data is not lost.
3. Stop the app, load the backup in one transaction (nothing changes if it fails), and start
   the app again:

   ```bash
   cd ~/noteworthy
   docker compose -f compose.yaml -f compose.staging.yaml stop app
   gunzip -c ~/restore.sql.gz \
     | docker compose -f compose.yaml -f compose.staging.yaml exec -T db \
       psql -U noteworthy -d noteworthy -v ON_ERROR_STOP=1 --single-transaction
   docker compose -f compose.yaml -f compose.staging.yaml start app
   ```
4. Check the row counts, then delete `~/restore.sql.gz`:

   ```bash
   docker compose -f compose.yaml -f compose.staging.yaml exec db psql -U noteworthy -d noteworthy \
     -c 'SELECT (SELECT count(*) FROM accounts) AS accounts, (SELECT count(*) FROM attempts) AS attempts'
   rm ~/restore.sql.gz
   ```

On a new Droplet, run `./scripts/deploy_staging.sh` first, so the database exists, then
follow the same steps.

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
3. Backup: run `./scripts/backup_staging.sh` and `./scripts/backup_db.sh` on the server and copy both
   files off with `scp`. Check that `crontab -l` lists `backup_db.sh` and that
   `~/backups/backup_db.log` ends with a recent `Saved` line.
4. Cost:
   - DigitalOcean Billing page: check this month's usage so far.
   - Model balance: the prepaid Inference and Agents balance must cover the demo. At $0 the model
     stops and answers fail until more is added.
   - Spend alert: Billing, Spend alerts. The $20 monthly alert is still set.
