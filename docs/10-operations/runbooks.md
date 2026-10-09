# Runbooks

The runbooks the operations document asks for (`OUTPUT.md`, "Runbooks"): its thirteen, two for the
single host (14 and 15), and one for the alerts the worker sends (16, added 2026-10-09 with DEC-078). They are written from the system as it is built on 2026-10-07; the parts for the
single host on 2026-10-08. **None has been executed**: there are no servers, no monitoring
system and no production environment yet (the deployment files of `deploy/production/` have run only on
a developer machine), so launch criterion 10 ("each executed once") is
open. Where a step cannot be carried out with what exists, it says so in a line starting **Not yet
possible**. A runbook is to be corrected the first time it is run for real.

**One host (changed by the founder on 2026-10-08, DEC-070).** For lack of budget there are no servers: the service runs on one computer
behind a Cloudflare Tunnel, with encrypted backups in Cloudflare R2 (`deploy/production/SINGLE-HOST.md`).
Runbooks 1 to 5 were written for two servers; each now has a part headed **On the single host**, which
is the one to follow today, and keeps its two-server text for the day there are two. Runbooks 14 and 15
are new and exist only for the single host. Runbooks 6 to 13 do not depend on where the service runs.
On the single host every command is `deploy/production/scripts/single-host.sh` (below: `single-host.sh`), run in Git Bash on the machine
from a checkout of the repository; its env file is `~/.qarz/single-host.env`.

Conventions used below:

- `primary` and `standby` are the two servers of ADR-014. Commands are run as the service's operating-system
  user unless a step says otherwise.
- The application is two processes of one code base: the API (`uvicorn qarz.interface.asgi:build --factory`)
  and the worker (`python -m qarz.interface.worker`), run as the services `api` and `worker` of
  `deploy/production/compose.yml` behind the `proxy` service. Both read their configuration from the
  environment; the names are listed in `.env.example` and, by service, in `deploy/production/.env.example`. Secrets live in the operator's password manager, never in the
  repository and never in a chat.
- "The owner connection" means a database session as the migration owner, which is not subject to
  row-level security. The application itself connects as three roles, one for each part: the API as
  `qd_app` (`QD_DATABASE_URL`) and, for the administrators' side, as `qd_admin`
  (`QD_ADMIN_DATABASE_URL`); the worker as `qd_worker` (`QD_WORKER_DATABASE_URL`). None of them can do
  what another is for (migration 0031).
- Every action an administrator takes in the panel is written to the admin audit by the application. What
  an operator does on a server is not: write it down in the incident or change note.

## 1. Deploy and roll back a release

**When.** A new version is to be put into service, or the one just deployed must be withdrawn.

The tooling is in `deploy/production/` (nginx and Docker Compose); its `README.md` has the one-time setup
of a host. All commands are run on the application host, from a checkout of the release, as the deploying
user.

1. Confirm that the commit to deploy is on `main` and that its CI run succeeded for that commit.
2. Read the migrations the release adds (`backend/migrations/sql/`). A migration that would make the
   previous release fail (a dropped or renamed column) must not be deployed in the same step as the code
   that needs it; the operations document requires migrations to be backward compatible.
   One existing migration is not: `0031_separate_roles` takes from the role `qd_app` what the
   administrators' side and the worker now do as `qd_admin` and `qd_worker`. The release that brings it
   needs the two new connection settings and the two new roles created with a login beforehand
   (`deploy/production/README.md`, "Database roles"), and rolling back across it needs one statement
   first (below).
3. Take note of the current release: `cat /var/lib/qarz/deploy/current`. That is the reference a roll
   back needs.
4. `deploy/production/scripts/deploy.sh <git-ref>`. It builds the two images of that commit (or reuses or
   pulls them), runs `alembic upgrade head` as the one-shot `migrate` service with the owner connection
   and prints the migration head, restarts the worker, then the API, then the proxy, and waits for
   `/healthz`. It stops at the first step that fails. Migration `0026_open_debt` fills a table from the
   whole ledger; on the generated load database of 3.69 million entries this took 52 seconds.
5. `deploy/production/scripts/smoke.sh https://<public host>`: `/healthz`, the redirect to HTTPS, the
   security headers, `/metrics` closed from outside, an unauthenticated call, the three pages, the body
   limits and the sign-in limit. Every line must say `ok`.
6. Check by hand what the script cannot: `/metrics` is answered inside the network (with the token); a
   test shop's overview opens in the Mini App; the bot answers `/start`.
7. Watch the error rate and the request log for fifteen minutes
   (`docker compose -p qarz logs -f proxy api worker`).

**Roll back.** `deploy/production/scripts/rollback.sh <previous-ref>` (the last release is in
`/var/lib/qarz/deploy/previous`), then `smoke.sh` again. It starts the previous images and runs no
migration. Do not run `alembic downgrade`: the migrations have no tested downgrade, and because they are
backward compatible the previous code runs on the newer schema. The exception is a roll back to a release
from before migration `0031`: run `GRANT qd_admin, qd_worker TO qd_app;` as the owner first, so that the
one role the old release connects as can again do everything, and `REVOKE qd_admin, qd_worker FROM qd_app;`
when the newer release is back.

**Not yet possible.** The scripts have run only on a developer machine, against a throwaway database and
with a self-signed certificate; there is no server, so steps 4 to 7 have never been done for real. There
is no image registry (images are built on the host), no staging, no restore point before a release and no
update of the standby's images, all of which the operations document asks for. Step 6 needs a real bot
and a monitoring system, and neither exists.

**On the single host (changed by the founder on 2026-10-08, DEC-070).** Steps 1 to 3 are the same, with the state in `~/.qarz/deploy/` instead of
`/var/lib/qarz/deploy/`. Then:

4. Take a backup first, so that the moment before the release can be had back:
   `single-host.sh backup diff`, and note the time. That is the restore point the operations document
   asks for.
5. `git pull` in the checkout so that it is at the commit to deploy, then `single-host.sh up`. It builds
   the images of that commit, runs the migrations, sets the roles' passwords from the env file again,
   restarts the worker, the API and the proxy (a few seconds without service), and leaves the database
   running unless the release changed the database image itself.
6. `single-host.sh smoke` (the public address, through Cloudflare) and `single-host.sh status`.
7. Step 6 above by hand, and watch `single-host.sh logs proxy api worker` for fifteen minutes.

Roll back: `single-host.sh rollback <previous-ref>`, then `single-host.sh smoke`. The note about migration
`0031` above applies; the statement is run with
`docker compose -p qarz exec db psql -U postgres -d qarz`.

Not yet possible on the single host either: none of this has been done on the real machine.

## 2. Fail over to the standby; rebuild a standby

**When.** The primary is lost or unreachable and will not come back within the recovery objective.

**On the single host (changed by the founder on 2026-10-08, DEC-070) there is no standby and this runbook does not apply.** Nothing can be
failed over to. If the machine is off or Docker is not running: runbook 14. If the machine or its disk is
gone: runbook 15, which is a restore from R2 onto another machine and loses whatever had not been
archived. The steps below are the two-server design's.

1. Establish that the primary is really down, from a third place if possible. A broken link between the
   servers looks the same from the standby; if the primary still serves users, do not fail over.
2. Make sure the old primary cannot come back as a writable database: power it off or block it at the
   provider. The local rehearsal used a fence file on a shared volume; two real servers share nothing, so
   this step has to be done by hand and is the one that prevents two primaries.
3. On the standby, promote the database (the rehearsal's `deploy/rehearsal/scripts/failover.sh` shows the
   sequence: wait for replay, fence, promote, first write).
4. Start the API and the worker on the standby with the same environment.
5. Point the service's name at the standby (DNS) and set the Telegram webhook to it again.
6. Check as in runbook 1, step 6. Tell shops which minutes may have to be entered again: writes that were
   acknowledged but not yet replicated are lost (in the rehearsal: none, on one machine).
7. Rebuild a standby from a fresh base backup as soon as a second server exists; until then there is no
   failover.

**Measured.** Only in containers on one machine: 20.5 seconds from the kill to the first accepted write
(`rehearsals/2026-10-06-local.md`). That says nothing about two real servers.

**Not yet possible.** Steps 2, 4 and 5 have never been done; setting the webhook has never been done at
all (no public address exists).

## 3. Point-in-time restore

**When.** Data was damaged by a person or by software, and the state just before the damage is needed.

1. Stop the damage first: if the application is still writing wrong data, stop the worker and the API.
2. Find the moment just before the damage (the shop's activity log, the admin audit, the request log by
   `request_id`).
3. Restore the backup repository into a **new** database instance up to that moment
   (`deploy/rehearsal/scripts/pitr.sh '<timestamp>'` shows the pgBackRest calls). Never restore over the
   running database.
4. Compare the restored data with the live data for the shops concerned, and decide with the founder what
   is copied back. Entries are never edited or deleted in the live ledger: a wrong entry is reversed
   through the product (runbook 6), and missing ones are entered again.
5. Tell the affected shops which minutes they must enter again.

Before step 3, see what there is to restore from: `pgbackrest --stanza=qarz info` on the standby lists the
backups, and `deploy/backup/scripts/check.sh` prints the age of the newest backup and of the newest
archived WAL segment. The moment wanted must lie after the end of a full backup whose WAL is still kept:
the last 14 days at least (`deploy/backup/README.md`, "Retention"). Older weekly backups and the monthly
dumps (`deploy/backup/scripts/monthly-archive.sh`) give the state of their own moment only.

The weekly restore test (`deploy/backup/scripts/restore-test.sh`) restores the latest backup and checks
the ledger in it. It is not a point-in-time restore: it stops where the backup ends and removes the
instance. A red restore test means this runbook may not work when it is needed; treat it the same day.

**Not yet possible.** The backup schedule, the restore test and the expiry exist as configuration and
scripts (`deploy/backup/`) and were proven in containers on one machine; they are installed on no server,
because no server exists. A point-in-time restore from a repository on a real standby has never been done.

**On the single host (changed by the founder on 2026-10-08, DEC-070).** The repository is the R2 bucket, and the new instance is a second
PostgreSQL container beside the live one, with its own volume and with archiving off.

1. Stop the damage, as in step 1: `docker compose -p qarz stop worker api` (the database keeps running,
   and keeps archiving).
2. Find the moment, as in step 2. Look at what can be restored: `single-host.sh status` prints the ages
   of the newest backup and of the newest archived WAL segment;
   `docker compose -p qarz exec backup pgbackrest --stanza=qarz info` lists the backups. The moment must
   lie after the end of a full backup whose WAL is still kept: the last 14 days at least.
3. `single-host.sh pitr '2026-10-08 14:05:00+05'` (the moment, with the time zone). It restores from
   the bucket into the volume `qarz_pgscratch` up to that moment and starts the copy. The live database
   is not stopped, read or changed. Restoring over the live database is not offered: `single-host.sh
   restore` refuses a volume that holds one.
4. Compare, as in step 4: `docker compose -p qarz exec db-scratch psql -U postgres -d qarz` for the
   copy, `... exec db psql ...` for the live database. What is copied back is decided with the founder
   and done through the product, never by editing the ledger.
5. `single-host.sh pitr-down` removes the copy and its volume. Start the API and the worker again
   (`single-host.sh start`). Tell the affected shops which minutes to enter again.

If the whole database must go back in time (not single shops), that is runbook 15 with
`single-host.sh restore --time '<moment>'` on this same machine, after the damaged volume has been put
aside by hand as that runbook says; everything recorded after the moment is then lost for every shop.

Proven only in containers with a stand-in for R2 (`deploy/production/scripts/single-host-proof.sh`, section 13):
a copy to a moment between two writes held the first and not the second. Never done on the real machine
or bucket.

## 4. Rotate bot token, webhook secret, server secret, database passwords, backup key

**When.** On a schedule, when a person with access leaves, or at once when a secret may have leaked.

| Secret | How | What it breaks |
|---|---|---|
| Bot token (`QD_BOT_TOKEN`) | Revoke and reissue with BotFather; set the new value; restart API and worker; set the webhook again | Until restart: sign-in and sending fail. Sessions already open keep working |
| Webhook secret (`QD_WEBHOOK_SECRET`) | Generate a new one; set the webhook with it; then restart the API with it | Between the two steps Telegram's calls are refused (403) and delivered again later |
| Server secret (`QD_SECRETS_KEY`) | See below | Nothing, when done in the order below |
| Database passwords | As the owner, for each role in turn: `ALTER ROLE qd_app PASSWORD ...` and `QD_DATABASE_URL`; `ALTER ROLE qd_admin PASSWORD ...` and `QD_ADMIN_DATABASE_URL`; `ALTER ROLE qd_worker PASSWORD ...` and `QD_WORKER_DATABASE_URL`. Each role has its own password. Restart the API after the first two and the worker after the third | Requests of that part fail between the change and the restart |
| Metrics token (`QD_METRICS_TOKEN`) | Set a new value; restart the API; update the monitoring system | Scrapes fail until both are changed |
| Backup key | See below | Losing the key loses the backups: keep it in two places off both servers |

**On the single host (changed by the founder on 2026-10-08, DEC-070)** the secrets live in one env file, and `single-host.sh up` is what applies
a changed value: it recreates the containers whose settings changed.

| Secret | How, on the single host | What it breaks |
|---|---|---|
| Bot token, webhook secret, server secret, metrics token | As in the table above and the steps below; "restart" is `single-host.sh up`; the commands that need the API's settings are run as `docker compose -p qarz exec api ...` | As above |
| Database passwords (`postgres`, `qd_app`, `qd_admin`, `qd_worker`) | Change the password **inside the connection string** in the env file (`QD_MIGRATION_URL`, `QD_DATABASE_URL`, `QD_ADMIN_DATABASE_URL`, `QD_WORKER_DATABASE_URL`; four different ones, 16 characters or more), then `single-host.sh up`. It sets each role's password from its connection string through the database's socket and restarts the services. No `ALTER ROLE` by hand | A few seconds without service |
| Tunnel token (`CLOUDFLARE_TUNNEL_TOKEN`) | In Cloudflare, Zero Trust, the tunnel: refresh its token (the old one stops working), or delete the tunnel and create a new one with the same public hostname and service `http://proxy:8080`. Put the new token in the env file and the password manager; `single-host.sh up` | Between the refresh and `up` the service is unreachable. Whoever held the old token could receive the service's traffic while it was valid: treat a leak as runbook 11 |
| R2 key (`DEPLOY_R2_ACCESS_KEY_ID`, `DEPLOY_R2_SECRET_ACCESS_KEY`) | In Cloudflare, create a new token limited to the bucket; put it in the env file and the password manager; `single-host.sh up` (the database restarts: its settings changed); `single-host.sh status` until the archive is fresh; then delete the old token | A few seconds without service. Between deleting an old token and `up`, nothing is archived |
| Backup passphrase (`DEPLOY_BACKUP_PASSPHRASE`) | See "The backup key" below | Losing it loses the backups: keep it in two places **off the machine** |

**The server secret needs care.** It derives the key that signs file links and the key that encrypts the
administrators' second-factor secrets. Changed on its own it makes every stored second-factor secret
unreadable. It is therefore rotated with the old secret kept beside the new one for a few minutes:

1. Generate the new secret (`.env.example` shows how) and store it in the password manager.
2. In the API's environment set `QD_SECRETS_KEY_PREVIOUS` to the secret in use and `QD_SECRETS_KEY` to the
   new one; restart the API. From here new file links are signed with the new key and links handed out
   a moment ago still open; administrators sign in with the device they have, because a stored secret the
   new key cannot read is tried under the previous one. Nothing new is signed or encrypted with the
   previous secret.
3. Re-encrypt the stored second-factor secrets, with the same two settings in the environment:
   `docker compose run --rm api python -m qarz.interface.rotate_secrets`. It works in one transaction,
   prints how many secrets it re-encrypted, how many were already under the new key and how many neither
   key could read, and never prints a secret. It can be run again: the second run re-encrypts none.
4. If it names accounts that neither key could read (exit status 1), those administrators are enrolled
   again (runbook 7, "An administrator"); nothing else was lost by the rotation.
5. Not sooner than five minutes after step 2 (the life of a file link), clear `QD_SECRETS_KEY_PREVIOUS`
   and restart the API. Run the command once more: it must report nothing re-encrypted and nothing
   unreadable beyond step 4. Then destroy the old secret.

While `QD_SECRETS_KEY_PREVIOUS` is set, whoever holds the old secret can still sign a file link. **After a
suspected leak** do not leave it in the API at all: stop the API, run step 3 with both settings given to
the command only, and start the API with the new secret alone. Administrators and file links are
unavailable for that minute, and links handed out before it stop working.

A wrong value in `QD_SECRETS_KEY_PREVIOUS` changes nothing: the command reports the secrets as readable
under neither key and leaves them as they were, so it can be run again with the right value. A secret
shorter than 16 characters in either setting refuses to start, the API and the command alike.

These steps are exercised by the test suite against a test database (`backend/tests/api/test_secret_rotation.py`);
they have never been carried out on a server.

**The backup key.** pgBackRest cannot change the passphrase of an existing repository. Where the key
lives and what it protects: `deploy/backup/README.md`, "The key".

1. Generate the new passphrase away from both servers and store it in the two places **before** using it.
2. On the standby, stop the timers (`systemctl stop 'qd-backup-*.timer' qd-restore-test.timer`), move the
   old repository aside (`/var/lib/pgbackrest` to a dated name), put the new passphrase into
   `/etc/pgbackrest/conf.d/cipher.conf`, run `pgbackrest --stanza=qarz stanza-create`, then
   `deploy/backup/scripts/backup.sh full` and `deploy/backup/scripts/restore-test.sh`.
3. Start the timers. Until the full backup of step 2 has ended there is no usable backup under the new key,
   and WAL archiving fails between moving the repository and `stanza-create`: do it at night, and expect
   the archive alert.
4. Keep the old repository and the old passphrase until the old backups have aged out (8 weeks; 12 months
   if the monthly dumps under the old key are to stay readable), then remove both. The file store archives
   and monthly dumps written from now on use the new passphrase; the earlier ones need the old one.

**Not yet possible:** these steps have never been run; they are written from the tools' documentation.

**The backup key on the single host (changed by the founder on 2026-10-08, DEC-070).** The passphrase of what is already in the bucket cannot
be changed, by pgBackRest or by rclone. A new passphrase means a new, empty bucket:

1. Generate the new passphrase (`openssl rand -hex 32`) and store it in the two places off the machine
   **before** using it.
2. In Cloudflare create a second bucket and a key limited to it.
3. In the env file set `DEPLOY_R2_BUCKET`, the two key settings and `DEPLOY_BACKUP_PASSPHRASE` to the new
   values; `single-host.sh up`. The database restarts, a repository is created in the new bucket, and
   the write-ahead log goes there from that moment.
4. At once: `single-host.sh backup full`, then `single-host.sh restore-test`. Until that full backup has
   ended there is no usable backup under the new passphrase. The stored files are copied to the new
   bucket by the next run of `files-backup` (five minutes).
5. Keep the old bucket, its key and the old passphrase until what they hold has aged out (8 weeks; 12
   months for the monthly dumps), then delete all three. The monthly dumps written before the change
   are encrypted with the old passphrase wherever they are copied.

Never run; written from the tools' documentation and from how the scripts behave with an empty bucket.

After any rotation following a suspected leak: review the admin audit and the request log for the period
of exposure, and treat it as runbook 11.

## 5. "The bot or the panel is not answering"

1. `/healthz` from outside. `down` or no answer: go to step 2. `ok`: go to step 4.
2. Are the processes running? Is the database reachable from the server? If the server itself is gone:
   runbook 2.
3. If the database is up and the API is not: read the API's log (JSON lines; look for `request_failed` and
   for errors at start). The API refuses to start on a webhook secret, metrics token or server secret that
   is too short, and on a rate limit that is not positive. Start it again; if it will not start on the
   current release, roll back (runbook 1).
4. The API answers but the bot is silent: is the worker running? Read `qd_outbox_oldest_due_seconds` in
   `/metrics`: a growing age means messages are queued and not sent. Read the worker's log for
   `dispatch_failed` and `schedule_failed`. Ask Telegram for the webhook's state (`getWebhookInfo`): a
   backlog or a last error there means Telegram cannot reach the API.
5. The bot answers but a screen does not: a `request_id` from the screen's error finds the request in the
   log. `503 TIMEOUT` means a statement ran longer than the limit (5 seconds); `429 RATE_LIMITED` means
   the user or the shop is over its rate.
6. Tell shops when it is a general outage (the notice text is the founder's; none is written yet).

**"The panel asked me to sign in again."** Three things end a web session before its 14 days, and each is
meant:

- The person signed in to the panel somewhere else. A person has one web session: a sign-in in another
  browser, or in this one after the page was loaded again, ends the earlier one. The Mini App is not
  affected, and the panel is not affected by the Mini App.
- The person, on any device, pressed "sign out everywhere". That ends every session of theirs and, for
  an administrator, the admin session too: the second factor is asked again after the next sign-in.
- **Nobody can sign in to the panel at all, and the Mini App still works:** the server's clock. The
  web login's signed data is accepted for five minutes after Telegram signed it
  (`QD_WEB_LOGIN_MAX_AGE_SECONDS`, 300), and not at all when it is dated more than a minute ahead; the
  Mini App's is given an hour. A clock some minutes off therefore stops the panel first. The log shows
  `bad_sign_in` for each refusal. Set the clock right (runbook 14, step 7). Only if the clock is right
  and the widget's data is still refused as too old, raise the setting, up to 3600, and restart the API.

**On the single host (changed by the founder on 2026-10-08, DEC-070)** there is one more layer in front, and one machine behind it:

- `/healthz` from outside gives **no answer at all, or Cloudflare's own error page** (error 1033 or
  530, "Argo Tunnel error", or a 502 from Cloudflare): the tunnel is not connected. Either the machine
  is off, asleep or offline, or Docker is not running, or the `cloudflared` container is not: runbook
  14. If the machine is fine and `docker compose -p qarz logs cloudflared` shows it connected, look at
  Cloudflare's own status and at the tunnel's page in Zero Trust.
- `/healthz` answers `down` (503 from the service itself): the API is up and the database is not.
  `single-host.sh status`, then `single-host.sh logs db`. A full disk stops PostgreSQL: the status prints
  the disk.
- Step 2's "if the server itself is gone" is runbook 15, not runbook 2.
- Steps 3 to 5 are unchanged; the logs are `single-host.sh logs api`, `... worker`, and the metrics are
  read with `docker compose -p qarz exec api python -c ...` as `deploy/production/scripts/local.sh` does.

## 6. "An entry is wrong"

Always a reversal through the product; never a change in the database.

1. Ask the shop to reverse the entry themselves: on the customer's page, or in chat within the allowed
   time. A manager or the owner can reverse what a seller cannot.
2. If they cannot (the member has left, the shop is in limited mode): find out why the product refuses,
   and fix that cause (for example the subscription), not the entry.
3. Never run `UPDATE` or `DELETE` on `ledger_entry`, `goods_line`, `promise` or `activity`. The application
   role has no such right; the owner connection has, and using it would break the one promise the ledger
   makes (REQ-N07).
4. If a figure on a screen disagrees with the entries (an overview total, a debtor's balance), check the
   stored open debts against the ledger as the owner:
   `SELECT * FROM open_debt_mismatches('<shop id>');` No rows means they agree. Rows mean the stored table
   is wrong, not the ledger: rebuild it for the customers concerned with
   `SELECT refresh_open_debts(ARRAY['<customer id>']::uuid[]);` and report it as a defect.

## 7. An owner lost their Telegram account; an administrator lost their device

**An owner.** Telegram is the only way to sign in (ADR-017), so a lost account is lost access.

1. Establish who is asking, outside Telegram: the founder decides what proof is enough (the phone number
   known to the service, the shop's details, a payment made from the owner's card). Write down what was
   accepted.
2. The person starts the bot from their new Telegram account, so that the service knows it.
3. On the founder's written decision an administrator opens the shop in the administrators' panel and
   uses "Change the owner" (`POST /api/admin/v1/shops/{shop_id}/owner`): the new account's Telegram
   identifier, the reason (what proof was accepted; it is kept in the admin audit and shown to nobody
   else), and a fresh code from the authenticator, which this action asks for every time. Somebody the
   service does not know and the present owner are refused. How many shops the person already owns does
   not matter (changed by the founder on 2026-10-08, DEC-065: until then a person who owned five was
   refused).
4. What it does, in one transaction: the new account becomes the owner; the old account stays in the shop
   as a **suspended manager**, so it can do nothing there (it may be in someone else's hands) until the new
   owner reinstates or removes it under Staff; a transfer the old owner had offered is cancelled. A
   suspended shop stays suspended. A shop waiting for deletion **keeps waiting with the same date**: the
   new owner is told the date and cancels the deletion in the panel if it was not theirs. An erased shop
   cannot be reassigned.
5. What is recorded and who is told: the admin audit (`shop.owner_reassigned`, with both user identifiers
   and the reason), the shop's own activity log, a message to the new owner, and a message to the old
   account that says only that the service's administration changed the shop's ownership. The operator
   gets the alert `ShopOwnerReassigned` and matches it to the founder's decision.
6. A mistake is corrected by the same action, back to the earlier account, with its own reason.

The action is covered by tests (`backend/tests/api/test_admin_owner.py`); it has never been used for a real
shop.

**An administrator.** A lost device means a lost second factor; it cannot be replaced through the API.

1. Remove the person's Telegram identifier from `QD_ADMIN_TG_IDS` and restart the API if the device may be
   in someone else's hands.
2. As the owner connection, clear that administrator's stored second-factor secret and confirmation, so
   that enrolment starts again; put the identifier back and restart.
3. The administrator signs in and enrols again; the new secret is shown once.
4. Review the admin audit for anything done with the lost device.

## 8. Review and decide subscription receipts; a suspected forged or reused receipt

**When.** An owner sent a receipt for a card transfer. An alert fires when one has waited 24 hours.

1. Open the receipt in the admin panel (Receipts), or use the buttons under its announcement in your
   private chat or in the review group. A press counts from an administrator who signed in to the
   panel with their code within the last eight hours. In the review group it also counts from any
   Telegram administrator of that group, whether or not they are a platform administrator (changed by
   the founder on 2026-10-08, DEC-064): Telegram is asked at that moment, and no answer is a refusal.
   Such a person is asked for a rejection's reason in their own chat with the bot, so they must have
   started the bot; the months cannot be corrected from the group. The decision is kept with their
   Telegram identifier in the receipt and in the admin audit. Anyone else's press changes nothing.
   **Give the administrator's role in the review group only to people who may decide receipts**, and
   take it away from anyone who should no longer. The bot itself must be an administrator of the
   group: Telegram promises an answer about another member only to a bot that is one, and without an
   answer every such press is refused.
2. Check the transfer in the card's own statement: the amount, the date, the sender. The image is never
   proof by itself. There may be several receiving cards (Settings, "cards to pay to"; up to ten, the
   first is the primary one the owner is shown first). The announcement and the receipt's page say
   which one the owner chose, by its label and the last four digits of its number ("Karta: Humo ·
   Anorbank ··9012"): look at that card's statement. No such line means the owner did not say (a
   receipt sent before there were several cards, or from a button of an old message): look at every
   card's statement. The line is what the owner chose, not proof of where the money went; a card that
   was renamed or removed since is still named as it was when the receipt was sent.
3. Look at the warning about copies: the same file sent before, by this or any other shop.
4. Approve with the months the money covers (correct the months in the panel if the owner stated them
   wrongly), or reject with a reason; the owner is told the reason word for word.
5. A decision cannot be changed. If an approval was wrong, set the paid-through date by hand on the shop's
   page, with a reason; if a rejection was wrong, ask the owner to send the receipt again.

**Suspected forgery or reuse.** Reject it. If it was already approved, correct the paid-through date and
consider suspension (the owner is told the reason you write). Note the receipt's identifier and the copies
shown; the file is kept three years.

**Changing the receiving cards.** In the panel's Settings: add a card with a label that tells the cards
apart to an owner (the kind and the bank, such as "Humo · Anorbank") and its sixteen digits; move a card
up to make it the primary one; remove a card that must not be paid to any more. Saving asks for the
authenticator code again, as the price does. The change applies at once: `/obuna` and the subscription
page show the new list, and a button of an older `/obuna` message that names a removed card shows the
list as it is now instead of asking for a receipt. The audit shows each card's label and last four
digits, never a whole number. Migration 0036 turned the one card number that was stored into a list of
that one card, labelled "Karta": give it a proper label.

**Not yet possible.** None of this has run with a real bot, a real group or a real card (launch
criterion 15). The admin panel's receipt screens were exercised against a fake server only.

## 9. Open and close support access

**When.** An owner asks for help that needs a look at their customers or entries.

1. In the admin panel, on the shop's page: open support access with the reason (the owner will read it)
   and the hours needed, at most 24. The owner is told at once in Telegram.
2. While it is open, the shop's customers and one customer's entries can be read, and nothing else;
   nothing can be changed. Every look is written to the admin audit and to the shop's activity log, which
   the owner sees.
3. Close it as soon as the work is done; it also ends by itself, and the owner can end it at any moment.
4. A read without an open access is refused, audited and counted (`admin_without_support_access`).

Never read a shop's data through the owner connection instead: that is exactly what support access exists
to make visible.

## 10. A customer asks to be removed, outside the product; a shop asks to be deleted

**A customer.** The product's own way is the customer's page: "remove my data". If the request arrives by
another route:

1. Establish that the person is the customer (they write from the Telegram account linked to the shop's
   record, or the shop confirms).
2. Ask them to use their page, or ask the shop to do it from the customer's card. If a debt is still owed,
   the removal waits until it is settled (BR-32; this rule has had no legal review).
3. What removal does: the link ends, the record is anonymized, receipts are marked for deletion and purged
   within the hour. Entries stay in the shop's ledger without the person's name.

**A shop.** Only the owner can ask, in the panel, by typing the shop's name. The shop is erased 30 days
later; until then the owner can cancel and can export. Offer the export before they confirm. Nobody,
including an operator with the application's database role, can shorten the 30 days: the database refuses.

## 11. Suspected data exposure

1. Stop the exposure before anything else: rotate what may have leaked (runbook 4), disable an
   administrator by removing their identifier from the allow-list, block an address at the proxy.
2. Establish what could have been read: the request log (which routes, which users and shops, by
   `request_id`), the admin audit, the security events (`shop_not_member`, `bad_sign_in`,
   `bad_webhook_secret`, `bad_second_factor`, `admin_without_support_access`).
3. Preserve the logs of the period; they are otherwise kept 30 days.
4. Decide with the founder who must be told and when. **Not yet possible:** the notification duties under
   Uzbekistan's law on personal data have not been established (launch criteria 2 and 3).
5. Write the incident down: what happened, what was exposed, what was done, what changes.

## 12. Switch on SMS or online payment

Both are built and switched off. Neither may be switched on without the founder's explicit decision, a
contract with the provider, and a registered entity.

**Online payment.**

1. Set the provider's keys in the environment (`QD_PAYME_*`, `QD_CLICK_*`) and restart the API.
2. **Before the switch:** run the provider's own test cabinet against `/pay/payme` and `/pay/click`. The
   adapters were written from the providers' published protocols and have never been run against either.
3. In the admin panel, Settings: turn `online_pay_on` on (the code is asked for again). It applies to all
   shops at once: there is no per-shop switch, so "one shop first" means a test shop while the keys are
   those of the provider's test mode.
4. Make one payment end to end and check the shop's paid-through date, the owner's message and the
   activity log.
5. To switch off: the same setting. Provider calls are then answered "disabled".

**SMS.** The provider is Eskiz (eskiz.uz; founder's decision of 2026-10-08) and the sender is built
(`backend/src/qarz/infrastructure/eskiz_sms.py`). It was written from Eskiz's published Postman collection
"СМС шлюз от Eskiz.uz" (https://documenter.getpostman.com/view/663428/RzfmES4z, as published 2023-11-13,
read 2026-10-08) and **has never been run against Eskiz**: every test uses a fake transport. An SMS
leaves only when all four hold: the Eskiz account is set in the worker's environment, the platform switch
`sms_on` is on, the shop turned SMS on for itself, and the shop has quota left this month
(`sms_monthly_quota`, 0 by default). Until then nothing is sent. While the free plan is on
(`free_plan_on`, below) there is a fifth: the shop is in a paid period. A shop on trial, a free shop and a
limited shop then send no SMS, and their owners are shown SMS as something paying adds.

1. **Contract.** A contract with Eskiz in the name of the registered entity, and money on its balance.
   **Not yet possible:** there is no registered entity.
2. **Sender name.** Eskiz sends from `4546` until an alpha name is registered with it. Decide the name,
   register it in the Eskiz cabinet, and use it as `QD_ESKIZ_SENDER` once Eskiz confirms it; until then
   the value is `4546`.
3. **Templates.** Eskiz sends only texts that match a template it has approved. Register every text of
   "Templates to register in the Eskiz cabinet" below and wait until each is approved as a service
   message (not advertising). A reminder whose text has no approved template is refused and is not
   retried.
4. **Variables.** Set `QD_ESKIZ_EMAIL`, `QD_ESKIZ_PASSWORD` and `QD_ESKIZ_SENDER` in the worker's
   environment (`deploy/production/.env.example`, "worker only"; the values live in the password
   manager) and restart the worker. All three or none: with any of them empty the worker logs
   `sms_partly_configured` once at start and sends nothing. The API is not given them. Nothing is sent
   yet: the switch is still off.
5. **Before the switch: one real message.** Eskiz's test mode accepts three fixed test texts only, so the
   reminder texts cannot be tried there. The first real SMS is therefore sent in production, to a phone
   the founder holds: set `sms_monthly_quota` to 1, and do steps 6 and 7 for a test shop only.
6. **The switch. Only the founder does this.** In the admin panel, Settings: set `sms_monthly_quota` (the
   number of SMS each shop may send a month), then turn `sms_on` on (the code is asked for again). It
   applies to every shop that has turned SMS on for itself; there is no way to open it to one shop only
   other than the shops' own switches, which are off by default.
7. **Check one message end to end.** In the test shop, a customer with the founder's phone number and no
   Telegram link, with something overdue; send a manual reminder. Check: the SMS arrives; its sender
   name; its text against the template; in the Eskiz cabinet, how many parts it was charged as; the
   worker's log has `sms_sent`; `qd_sms_messages_last_day{status="sent"}` is 1.
8. **To switch off:** the same setting. The very next message is not sent. Messages already queued are
   tried again with the outbox's backoff and given up after a day, so switching on again within that day
   sends them late.
9. **To change the Eskiz password:** change it in the cabinet, set `QD_ESKIZ_PASSWORD`, restart the
   worker. The token Eskiz gives (30 days) is held only in the worker's memory: it is never logged and
   never stored, and a restart obtains a new one with the first message.

### The cash book (`cash_book_on`)

Off by default; turning it on or off asks for the second factor. It needs migration `0042` and nothing
else: no setting of a shop, no environment variable, no restart.

1. **What turning it on does.** Every shop's managers and owner get the section "Kassa" in the Mini App
   and the panel and the command `/kassa` in the bot; the payment form asks how the money came. From that
   moment each customer's payment is also written into the shop's cash book as income ("Qarz qaytdi").
   A shop's categories are written the first time its book is used, in the shop's language.
2. **What it does not do.** It copies nothing from the past: a shop's book starts empty. An owner who
   wants earlier payments in the book presses "Avvalgi to'lovlarni kassaga ko'chirish" under "Toifalar"
   (optionally from a chosen day). It can be pressed again safely; it writes each payment once.
3. **Turning it off** hides the section, the command and the method again and stops new payments from
   being written into the book. Nothing is deleted: the entries are there when it is turned on again.
   A payment that was in the book and is reversed while the switch is off is still cancelled in the
   book. Payments recorded while it was off are missing from the book until an owner copies them in.
4. **"A cash book entry is wrong."** Nobody edits or deletes one, operators included. A manager or the
   owner cancels it with a reason and records the right one; the cancelled entry stays in the list. An
   entry that is a customer's payment is cancelled by reversing that payment on the customer's page
   (runbook 6); recording the payment again with the right method writes the right entry.
5. **"The balance is below zero" or "does not match the till."** The book holds what was written into
   it. A shop that started with money in the till records it once as income in "Boshlang'ich qoldiq",
   dated the day it starts.

### The free plan (`free_plan_on`, `free_plan_customers`)

Built and switched off (expansion module A; domain rules BR-33 to BR-35). Like every module of the
expansion it is switched on only by the founder's decision, in the admin panel, Settings; the code is
asked for again. `free_plan_customers` (30 by default, 1 to 10 000) is how many customers the plan holds.
It applies to every shop at once.

What changes the moment it is on:

- A shop whose trial or paid period has ended, or that never had one, and that has no more **active**
  customers than the number, is free: it works in full and `/obuna` and the subscription page say "free
  plan", with the customers used. Archived customers are not counted. Nothing is written to
  `subscription`: its `state` stays `limited`, meaning "no period runs", and the admin panel's list of
  shops shows such a shop as limited.
- A shop without a running period cannot go over the number: the next customer, a customer taken out of
  the archive, or an import that would exceed it is refused with `FREE_PLAN_FULL`, in the panel and in
  the bot, with the number and `/obuna`.
- A shop over the number whose period ends becomes limited exactly as before. The daily review at 09:00
  tells each owner which of the two happened, and the warnings seven days and one day before say which
  will.
- SMS, if `sms_on` is on, is sent only for shops in a paid period (above).

Before the switch: decide `free_plan_customers` first, then turn `free_plan_on` on. Raising the number
later makes limited shops under the new number free at once; lowering it makes free shops over the new
number limited at once, with no message to their owners, so tell them before lowering it.

To switch off: the same setting. Every free shop is limited again at once, as it was before the plan,
and shops on trial and limited shops send SMS again if they had turned it on.

### When SMS fail

The worker logs one line for each attempt, with no number, no text and no secret: `sms_sent`,
`sms_rejected` (failed for good) or `sms_retry` (will be tried again), with `kind` and the HTTP `status`.
`/metrics` gives `qd_sms_messages_last_day{status="sent"|"failed"|"retrying"}` and the rules `SmsRefused`
and `SmsNotGoingOut` read it.

| What happened | `kind` | Outcome in the outbox |
|---|---|---|
| Eskiz answered 2xx | (`sms_sent`) | Sent |
| The number is not an Uzbek one (`+998` and nine digits) | `not_uzbek_number` | Failed at once; Eskiz is not asked |
| Eskiz answered 400, 402, 403, 404, 422 or any other 4xx not named below, or 2xx with `"status": "error"` | `rejected` | Failed at once, not retried: the number, a text without an approved template, or an empty balance |
| Eskiz answered 401 | - | One new sign-in and one more attempt; if that is 401 too: `unauthorized`, retried later, and no sign-in for 5 minutes |
| Eskiz answered 429 | `rate_limited` | Retried with backoff (5 s, 10 s, 20 s ... up to 1 hour), given up after 24 hours |
| Eskiz answered 408, 5xx or a redirect | `provider_unavailable` | The same |
| No answer within 20 seconds; connection failed | `timeout`, `network` | The same |
| The sign-in was refused (4xx) | `sign_in_refused` | The same; no sign-in is tried for 5 minutes (`sign_in_paused`), so a wrong password is not repeated for every message |
| The sign-in failed otherwise | `sign_in_failed` | The same |
| The account is not configured, or `sms_on` is off | (no line) | The same: nothing is sent |

- `SmsRefused` with `kind="rejected"` in the log: look in the Eskiz cabinet for the reason. An empty
  balance fails every reminder until it is topped up, and those reminders are not sent later: the shop
  sees them as sent by SMS. A changed wording in the code needs its template approved first.
- `SmsNotGoingOut`: Eskiz is unreachable, the password is wrong (`sign_in_refused`), or the switch was
  turned off with messages queued.

**Known limits, for the founder to decide on.**

- Eskiz's document names no error answer, no rate limit and no answer for an expired token. The table
  above is this code's reading of HTTP statuses and is unproven; correct it after the first real failures.
- Delivery reports (`callback_url`) are not taken: "sent" means Eskiz accepted the message, not that the
  phone received it. Eskiz's callback carries the phone number and is not signed.
- A reminder that failed and is retried may be accepted hours later, outside the reminder hours (08:00 to
  20:00): the outbox's retry does not know the hour. The same holds for Telegram.
- A customer whose stored number is not an Uzbek one is still chosen for SMS and counted against the
  quota; the message then fails at once.
- The Russian texts, and any text with a customer or shop name in Cyrillic or with `ʻ`, `ў`, `ғ`, are sent
  as Unicode: 70 characters a part instead of 160, so most are charged as two parts.

### Templates to register in the Eskiz cabinet

The SMS texts are a short fixed form in each language, whatever wording the shop chose for Telegram
(DEC-035). These are all the texts the code can send by SMS, exactly as it produces them
(`backend/src/qarz/application/chat_texts.py`; `backend/tests/test_sms_templates.py` fails when this
table and the code differ). `{shop}` is the shop's name, `{name}` the customer's name as the shop wrote
it, `{amount}` an amount such as `70 000 so'm` or `1 250 000 сум`: digits in groups of three separated by
ordinary spaces, then the currency word of the language. How Eskiz wants a variable part written in a
template is not in its document: ask Eskiz when registering.

**Uzbek and Russian only.** The product speaks six languages, and an SMS speaks two: a wording may be sent
only after Eskiz has approved it, and only these four are registered. A customer whose language (or whose
shop's language) is Uzbek Cyrillic, Tajik, Karakalpak or English is sent the Uzbek text, whole, with the
Uzbek currency word. No other language has an SMS text in the code, and the same test fails if one is
added. To send SMS in another language: write its two texts, register them here and with Eskiz, and add
the language to `qarz.domain.languages.SMS_LANGUAGES`.

| Text | Language | Template |
|---|---|---|
| `sms_due_today` | uz | `{shop}: {name}, bugun {amount} to'lash kuni. Rahmat.` |
| `sms_overdue` | uz | `{shop}: {name}, {amount} qarz muddati o'tgan. Iltimos, to'lab qo'ying.` |
| `sms_due_today` | ru | `{shop}: {name}, сегодня срок оплаты {amount}. Спасибо.` |
| `sms_overdue` | ru | `{shop}: {name}, срок оплаты долга {amount} прошёл. Пожалуйста, оплатите.` |

The wordings themselves are agent drafts awaiting the founder's review (DEC-035).

**Dollars.** A shop that works in dollars is reminded of dollars by Telegram only. The four registered
texts state one amount in so'm, so an SMS is the reminder of the so'm debt alone: it is planned from the
so'm debt as if the shop had no dollars, it states the so'm amount and nothing else, and none goes out
when only dollars are due. The code refuses to make an SMS text with a dollar amount in it, and a test
holds it to that. The staff are told what an SMS leaves out: under the SMS switch on the reminders
screen, after a reminder sent by hand ("the SMS stated so'm only; 12.50 $ was not mentioned"), and in
the list of customers who cannot be reached, which names every customer without Telegram whose dollar
debt is due.

To let an SMS carry dollars, the founder asks Eskiz one question when registering: may the variable
part `{amount}` of the approved templates also be `12.50 $`, or `70 000 so'm va 12.50 $` (Russian:
`70 000 сум и 12.50 $`)? If Eskiz says yes, no new template is needed and the change is in
`qarz.application.reminders` only (the SMS takes the whole plan and `both()` writes the amount). If
Eskiz wants the unit to be part of the fixed text, eight more templates are registered first (dollars
only, and both amounts; due today and overdue; Uzbek and Russian), added to the table above and to the
catalog, and only then sent. Until one of the two is done, nothing changes here.

## 13. Onboard a shop, including its paper ledger

1. The owner starts the bot, opens a shop and gets the trial (if `trial_on`). While the free plan is on
   (`free_plan_on`, runbook 12), a shop without a trial, such as a person's second shop, starts free.
2. Staff: the owner invites sellers and managers from the panel; each accepts in the bot.
3. The counter code: printed and placed where customers can scan it; each customer agrees to the consent
   text before anything is shown to them. **The consent text has had no legal review.** A shop's
   waiting list holds a hundred people who came in the last 24 hours; while it is full the bot tells the
   next person so and keeps nothing about them. Staff make room by attaching or dismissing those who
   wait. A list filled by strangers means the code got out: replace the counter code (the old one stops
   working at once), dismiss the entries, and print the new one.
4. The paper ledger: download the import template from the panel, fill it (name, phone, amount, promised
   date, note; at most 2 000 rows a file), upload it, read the preview, and apply. Rows that match more
   than one existing customer block the import until the file is corrected. An applied import can be
   undone as a whole until a payment is recorded against it.
5. Check the overview against the paper ledger's total with the owner.
6. Explain what customers will receive and how a dispute or a request to move a date reaches the shop.

**Not yet possible.** No real shop may be onboarded before the launch criteria are met and the founder
approves the launch. The import screen was exercised against a fake server only. The template has not
been opened in a spreadsheet program, nor a file written by one been read.

## 14. "The computer was off": power loss, a restart, Docker not running

For the single host only (changed by the founder on 2026-10-08, DEC-070). **When.** The service does not answer and the cause is the machine:
the electricity went, Windows restarted (an update, a crash), somebody shut it down, or Docker Desktop is
not running. Nothing has been lost in any of these: a recorded entry is on the disk before the seller is
answered, and PostgreSQL repairs itself from its own log when it starts.

1. Get the machine on. If it does not start by itself when the power returns, the BIOS setting of
   `deploy/production/SINGLE-HOST.md`, step 2, is not set.
2. Docker Desktop starts when somebody signs in to Windows (unless the automatic sign-in of that same
   step was chosen). Sign in and wait until Docker says it is running.
3. Do nothing else for two minutes. Every container of the project restarts by itself, in order: the
   database, then the API and the worker, the proxy, the tunnel, the backups.
4. `single-host.sh status`. Every service must be `Up`, the database, the API and the proxy `healthy`.
   If some are missing or `Exited`: `single-host.sh start`, then status again.
5. Open `https://<host>/healthz` from a phone on mobile data: `{"status":"ok"}`. Send `/start` to the
   bot. Telegram delivers the updates it could not deliver while the machine was off, for as long as it
   keeps them; `getWebhookInfo` shows how many are waiting and the last error.
6. Backups catch up by themselves: the scheduler takes the backup that was due while the machine was
   off, and the write-ahead log is sent again within minutes. `single-host.sh status` must show the
   newest WAL segment younger than five minutes within a quarter of an hour; the `backup` container
   turns healthy again when it does.
7. Check the clock (`date` in Git Bash against a phone). After sleep or a long stop Docker's clock can
   be behind, and sign-in then fails for everybody: restart Docker Desktop if it is.
8. Write down when the outage began, when it ended and why. That list is the only availability
   measurement there is.

**If the database does not start** (`single-host.sh logs db` shows it failing again and again): do not
delete anything, and do not run any "clean", "purge" or "reset" in Docker Desktop. Stop everything
(`single-host.sh stop`) and go to runbook 15, "On the same machine".

**If Docker Desktop itself does not start:** restart Windows once. If it still does not, its repair may
offer to reset or delete its data: **that deletes the database**. Before accepting, treat the machine as
lost and follow runbook 15 on another machine; the data in R2 is safe whatever is done here.

**Never done.** No power cut has been tried on the real machine. In the proof the containers were
stopped and started, not the machine.

## 15. Move the service to another machine from R2 alone

For the single host only (changed by the founder on 2026-10-08, DEC-070). **When.** The machine or its disk is lost, stolen or dead; or Docker's
data was wiped; or the service is being moved on purpose. Everything needed is in two places: the R2
bucket, and the founder's password manager. **Without the backup passphrase this runbook cannot be done
and the data is gone.**

What is lost: the entries whose write-ahead log had not reached R2 (normally the last minute or two; if
the old machine's internet link was down before it died, everything since then), and the files stored
in the last five minutes.

1. **The old machine must not run the service at the same time.** If it still works, run
   `single-host.sh stop` on it and do not start it again. Two machines with one tunnel token both receive
   visitors, and two databases must never archive into one bucket.
2. Prepare the new machine: Docker, Git and Git Bash (or any Linux with Docker and bash), and the
   preparation of `deploy/production/SINGLE-HOST.md`, step 2.
3. Clone the repository and check out the commit that was running (the newest of `main` if that is not
   known; a newer release migrates the restored database forward when it starts).
4. The env file, at `~/.qarz/single-host.env`, mode 600:
   - if the password manager holds a copy of the whole file, use it as it is;
   - if it holds only single secrets: `single-host.sh env-init`, then replace the generated
     `DEPLOY_BACKUP_PASSPHRASE` with the **real** one, fill in the bucket, its key, the tunnel token
     and the bot token, and replace `QD_SECRETS_KEY` and `QD_WEBHOOK_SECRET` with the real ones (with a
     new server secret every administrator must enrol the second factor again, runbook 7; with a new
     webhook secret the webhook must be set again). New database passwords are fine: they are set from
     the file in step 7.
5. `single-host.sh restore`. It builds the database image, checks that the data volume is empty, and
   restores the newest backup and every archived WAL segment after it from the bucket. For a moment
   earlier than the end of the archive: `single-host.sh restore --time '<moment with time zone>'`.
6. `single-host.sh restore-files`: the stored files, from the bucket.
7. `single-host.sh up`. The database replays the archive and opens; the passwords are set from the env
   file; the migrations run (nothing to do when the release is the same); the API, the worker, the
   proxy, the backups and the tunnel start. Nothing changes in Cloudflare or at Telegram: the tunnel's
   token and the public name are the same.
8. Check as in `SINGLE-HOST.md`, step 8: `single-host.sh status`, `single-host.sh smoke`, the bot, the
   panel. Then `single-host.sh backup full` and `single-host.sh restore-test`: the restored database is
   on a new timeline and needs a backup of its own.
9. Find the last entry that came back (the newest `created_at` in the ledger, the shop's activity log)
   and tell the shops from which minute they must enter again.
10. Write down how long each step took. Until this has been done once on a second machine, nobody
    knows the recovery time (launch criterion 8, proposed).

**On the same machine** (the database volume is damaged, or the whole database must go back to an
earlier moment): `single-host.sh restore` refuses a volume that holds anything, on purpose. Put the old
volume aside by hand first, so that nothing is destroyed before the restore has worked:

```sh
single-host.sh stop
docker compose -p qarz rm -f db backup
docker volume create qarz_pgdata_aside
# a copy of the volume, file for file (MSYS_NO_PATHCONV: Git Bash must not rewrite the container's paths)
MSYS_NO_PATHCONV=1 docker run --rm --user 0 --entrypoint cp \
  -v qarz_pgdata:/from:ro -v qarz_pgdata_aside:/to postgres:16.15-bookworm -a /from/. /to/
docker volume rm qarz_pgdata          # the original; from here only the copy and the bucket hold the data
single-host.sh restore                # or: restore --time '<moment>'
single-host.sh up
```

Remove `qarz_pgdata_aside` only when the restored service has been checked.

**What has been proven and what has not.** In containers, with a MinIO server standing in for R2: the
database volume was destroyed, `single-host.sh up` refused to start an empty database in front of the
bucket's backups, a restore with a wrong passphrase restored nothing, `single-host.sh restore` and `up`
brought back every row, those written after the last backup included, and the stored files came back
byte for byte (`deploy/production/scripts/single-host-proof.sh`, sections 8 and 14; CI job `single-host`).
**Not proven:** any of it on a second real machine, against the real bucket, by a person, with a clock.
Of the "On the same machine" commands, only the copy of a volume was tried (on the proof's own volume: the
same number of files, the same owner); the sequence as a whole has never been run.

## 16. An alert arrived in the operators' chat; the test alert; the notification from outside

Added on 2026-10-09 (the founder's decision, DEC-078). **None of it has been executed against real
Telegram or on the real machine.** The worker watches the service every minute and writes to the
Telegram chats named in `QD_ALERT_CHAT_IDS` (`deploy/production/SINGLE-HOST.md`, "What is watched, and
what is not", has the full table and the thresholds). A message has three kinds of line: 🔴 it began,
🟠 it still goes on (every four hours), 🟢 it stopped, with from when to when. A line names a rule and
sometimes a label, never a shop or a person.

### Once, after the first deployment (launch criterion 9)

1. Put your numeric Telegram identifier, or a group's (negative; add the bot to the group first), into
   `QD_ALERT_CHAT_IDS` in the env file, and run `single-host.sh up` so that the worker reads it. A person
   must have sent `/start` to the bot once, or Telegram refuses the message.
2. `single-host.sh alert-test`. It prints `ACCEPTED` or `NOT DELIVERED` with the reason for every chat.
   **Then look in the chat**: the criterion is "triggered and received", and the command can only know
   the first half. Write down the date and that you saw it.
3. See one real alert arrive and stop: `docker compose -p qarz stop files-backup`, wait about 25 minutes
   for `FilesCopyStale`, `docker compose -p qarz start files-backup`, wait for the 🟢.
4. **Turn on Cloudflare's notification for the tunnel.** Cloudflare dashboard, *Notifications*, *Add*,
   **Tunnel Health Alert**, this tunnel, your e-mail. This is the only thing that tells you when the
   machine is off, offline, or Docker is not running: the worker's watch runs on that machine and is
   silent then. Test it: `single-host.sh stop`, wait for the e-mail, `single-host.sh start`. Optional
   and worth it: an uptime service asking `https://<host>/healthz` every minute.

After any change of the bot's token or of the chats, repeat step 2.

### When an alert arrives

`single-host.sh status` shows the containers, the backups and what is firing; `single-host.sh logs
worker` shows the watch's own lines (`ops_alert_firing`, `ops_alert_resolved`, `ops_alert_not_sent`).

| The message says | Look at | Then |
|---|---|---|
| `BackupMissing`, `BackupFailed`, `RestoreTestNotPassed`, `RestoreTestFailed` | `single-host.sh logs backup`: the job's JSON line and the lines before it | Usually the bucket cannot be reached (the link, the R2 key) or the disk is full. Fix that, then `single-host.sh backup full` or `restore-test`. A restore test that fails on a good link is serious: the newest backup may not be restorable; take a new full backup and test again, and do not delete anything |
| `WalArchiveStale` | The same log; `single-host.sh status` | The database is not sending its log to the bucket, or the check is not running. Until it is fixed, a lost disk loses everything since the last archived segment. If the link is down, it catches up by itself when the link returns |
| `FilesCopyStale` | `single-host.sh logs files-backup` | The same causes. Receipts uploaded since the last copy exist on this machine only |
| `DiskAlmostFull` | `docker system df`; `single-host.sh status` | Free space before the database stops: old images of earlier releases (`docker image ls`), never a volume. Runbook 14 has what else lives on that disk |
| `OutboxOld`, `DispatcherFailing` | `single-host.sh logs worker` | Messages are not going out. With `TelegramUnreachable` beside it: the link, or Telegram. With `TelegramRefusesBot`: the token (runbook 4). Otherwise restart the worker (`docker compose -p qarz restart worker`) and read why it stopped. Nothing is lost: messages wait up to 24 hours |
| `TelegramRefusesBot`, `TelegramUnreachable` | These reach you only after they stopped (the alert could not be sent while they held) | Read the period in the 🟢 line and check what did not go out meanwhile; runbook 5 |
| `RemindersNotRunning`, `JobNotRunning:<job>` | `single-host.sh logs worker`, lines `schedule_failed` | A scheduled job fails every time it is tried. The error names the place. `ledger_check` or `stock_check` failing alone on a large database means its one statement takes longer than the worker's 60 seconds: tell the developer |
| `LedgerMismatch` | Nothing in the panel shows it; the figure in the message is in how many places | The stored open debts differ from the ledger somewhere. The ledger is the truth and the stored figures can be rebuilt from it (`refresh_open_debts`, as the owner). **Do not edit anything by hand**: this should never happen and means a defect, so tell the developer first |
| `StockMismatch:stock_level`, `StockMismatch:supplier_balance` | Nothing in the panel shows it; the figure in the message is in how many places (items, or suppliers and currencies). Checked once a day, just after midnight, while `stock_on` is on. As the owner of the database, `SELECT * FROM stock_level_mismatches(NULL)` or `SELECT * FROM supplier_balance_mismatches(NULL)` lists them: the stored figure beside what the movements or the entries add up to | What the stock keeps on hand (`stock_level`), or what a shop owes a supplier (`supplier_balance`), differs from the movements or the entries it is the sum of. Both are written by triggers alone, so this means a defect or a hand in the database. The movements and the entries are the truth; the affected shops see a wrong quantity, value or debt until it is repaired. **Do not edit anything by hand** and do not cancel documents to "fix" it: keep the output of the two statements and tell the developer, who repairs the kept row from its ledger in one transaction. The alert clears at the next daily check. Turning `stock_on` off does not clear it: the last count stays |
| `ReceiptsWaiting` | The panel, receipts | Runbook 8 |
| `SmsRefused`, `SmsNotGoingOut` | Runbook 12, "When SMS fail" | |
| `ApiDown`, `MetricsMissing`, `ErrorRateHigh` | `single-host.sh logs api`; `single-host.sh status` | Runbook 5. `ErrorRateHigh` right after a release: runbook 1, roll back |
| `CrossTenantAttempt`, `InvalidSignaturesRepeated`, `AdminSecondFactorRepeated`, `AdminWithoutSupportAccess` | `single-host.sh logs api`, lines with `"event":"security"`: they carry the request identifier, the user and the shop | One `CrossTenantAttempt` is often a member who was just removed. Repeated, or with the others: runbook 11 |
| `SupportAccessOpened`, `ShopOwnerReassigned` | The admin audit in the panel | Expected now and then; you should be able to name the reason for each (runbooks 9 and 7) |
| "the worker cannot reach the database" | `single-host.sh status`, `single-host.sh logs db` | Runbook 5, then 14. While it lasts nothing else is watched |

### When no alert arrives and one should have

- `single-host.sh alert-test`: `NOT DELIVERED` says whether it is the token, the chat or the network.
- `single-host.sh status`: under "operations watch", `FIRING, nobody told (unconfigured)` means
  `QD_ALERT_CHAT_IDS` is empty in the env file the worker was started with.
- The worker is not running or is restarting (`single-host.sh status`): then nothing is watched at all,
  and nothing says so. This is the gap the watch cannot close for itself.

**What has been proven and what has not.** In tests, with Telegram replaced: every condition fires and
does not fire at its threshold; an alert is told once, repeated after four hours, taken back once, kept
quiet with no chat configured, and stays owed while Telegram fails. In containers
(`single-host-proof.sh`, section 7a; CI job `single-host`): a WAL figure made stale by hand became a
firing alert in the worker's table, the failed attempt to send it was recorded (every way out of the
stack is closed), and it stopped when the figure was fresh again. **Not proven:** a message arriving in
a real Telegram chat, the Cloudflare notification, and any of the steps above on the real machine.

## Stock (expansion module I)

Turning it on: the administrator's panel, setting `stock_on` (asks for the second factor). Nothing is migrated or backfilled: every item starts not counted, and a shop counts an item by receiving it or by turning its counting on. Turning it off hides the module and stops sales from moving stock; what was recorded stays, cancelling a sale made while it was on still puts its goods back, and the owner's export still carries the stock sheets.

If a shop doubts a quantity or a supplier's balance, compare the kept figures with their ledgers as the database owner (the functions are granted to no application role):

```sql
SELECT * FROM stock_level_mismatches('<shop id>');      -- empty when what is on hand equals the sum of the movements
SELECT * FROM supplier_balance_mismatches('<shop id>'); -- empty when what is owed equals the sum of the entries
```

Both must be empty: the figures are written only by the triggers `stock_movement_apply` and `supplier_entry_apply`. A row here means someone changed a table by hand; nothing in the application can. Do not correct `stock_level` or `supplier_balance` by hand either: a wrong quantity is corrected by a stocktake, a wrong receipt or payment by cancelling it.

**Sales for cash (migration 0049).** With `stock_on` on, the counter records a sale without a customer (`stock.sell`, every member by default); it takes counted goods out of the stock and, while `cash_book_on` is on, writes income to the cash book under "Ombor: naqd savdo". Nothing is switched on separately and nothing is backfilled: goods sold for cash before this existed are still on the books, and a shop brings them into line with a stocktake. A wrong sale is cancelled by a manager or the owner with a reason (`stock.sell.cancel`); it is never deleted, and its cash entry cannot be cancelled in the cash book itself. If a shop says "the till and the sales differ": the sales of a day and their totals by method are in the stock's sales list; with the cash book off, or for sales made while it was off, there is no cash entry by design. As the database owner, a sale that stands without its entry while the cash book was on would be found by:

```sql
SELECT d.id, d.number, d.total FROM stock_document d
 WHERE d.shop_id = '<shop id>' AND d.kind = 'sale' AND d.status = 'posted'
   AND NOT EXISTS (SELECT 1 FROM cash_entry e WHERE e.stock_document_id = d.id AND e.cancelled_at IS NULL);
```

Rows here are sales made while the cash book was off (compare `created_at` with when the switch was turned); nothing in the application can post a sale and skip its entry while it is on, because both are one transaction.
