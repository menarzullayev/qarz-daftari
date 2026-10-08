# Runbooks

The thirteen runbooks the operations document asks for (`OUTPUT.md`, "Runbooks"). They are written from the
system as it is built on 2026-10-07. **None has been executed**: there are no servers, no monitoring
system and no production environment yet (the deployment files of `deploy/production/` have run only on
a developer machine), so launch criterion 10 ("each executed once") is
open. Where a step cannot be carried out with what exists, it says so in a line starting **Not yet
possible**. A runbook is to be corrected the first time it is run for real.

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

## 2. Fail over to the standby; rebuild a standby

**When.** The primary is lost or unreachable and will not come back within the recovery objective.

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
   proof by itself.
3. Look at the warning about copies: the same file sent before, by this or any other shop.
4. Approve with the months the money covers (correct the months in the panel if the owner stated them
   wrongly), or reject with a reason; the owner is told the reason word for word.
5. A decision cannot be changed. If an approval was wrong, set the paid-through date by hand on the shop's
   page, with a reason; if a rejection was wrong, ask the owner to send the receipt again.

**Suspected forgery or reuse.** Reject it. If it was already approved, correct the paid-through date and
consider suspension (the owner is told the reason you write). Note the receipt's identifier and the copies
shown; the file is kept three years.

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
(`sms_monthly_quota`, 0 by default). Until then nothing is sent.

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

| Text | Language | Template |
|---|---|---|
| `sms_due_today` | uz | `{shop}: {name}, bugun {amount} to'lash kuni. Rahmat.` |
| `sms_overdue` | uz | `{shop}: {name}, {amount} qarz muddati o'tgan. Iltimos, to'lab qo'ying.` |
| `sms_due_today` | ru | `{shop}: {name}, сегодня срок оплаты {amount}. Спасибо.` |
| `sms_overdue` | ru | `{shop}: {name}, срок оплаты долга {amount} прошёл. Пожалуйста, оплатите.` |

The wordings themselves are agent drafts awaiting the founder's review (DEC-035).

## 13. Onboard a shop, including its paper ledger

1. The owner starts the bot, opens a shop and gets the trial (if `trial_on`).
2. Staff: the owner invites sellers and managers from the panel; each accepts in the bot.
3. The counter code: printed and placed where customers can scan it; each customer agrees to the consent
   text before anything is shown to them. **The consent text has had no legal review.**
4. The paper ledger: download the import template from the panel, fill it (name, phone, amount, promised
   date, note; at most 2 000 rows a file), upload it, read the preview, and apply. Rows that match more
   than one existing customer block the import until the file is corrected. An applied import can be
   undone as a whole until a payment is recorded against it.
5. Check the overview against the paper ledger's total with the owner.
6. Explain what customers will receive and how a dispute or a request to move a date reaches the shop.

**Not yet possible.** No real shop may be onboarded before the launch criteria are met and the founder
approves the launch. The import screen was exercised against a fake server only. The template has not
been opened in a spreadsheet program, nor a file written by one been read.
