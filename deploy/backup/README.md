# Backups: schedule, restore test, monitoring

The configuration and scripts that implement the "Backup" section of `docs/10-operations/OUTPUT.md`
(ADR-015 for the database, ADR-020 for files): pgBackRest configuration for two servers, systemd timers,
a weekly restore test that checks the restored ledger, expiry, the figures and alert rules for
monitoring, and the copy of the file store.

> **This is the design for two servers, not the current deployment.** By the founder's decision of
> 2026-10-08 (DEC-070) the service runs on one machine, and its backups go to a Cloudflare R2 bucket:
> `deploy/production/SINGLE-HOST.md`, "Backups". There is no standby, so the configuration for two hosts
> (`pgbackrest.conf`, `pgbackrest-primary.conf`, `postgresql-archive.conf`), the systemd units of
> `systemd/` and `filestore-sync.sh` are **not installed anywhere and not used today**. They stay for
> the day there are two servers.
>
> What the single host does use from here, unchanged, inside its database image: `scripts/backup.sh`,
> `restore-test.sh`, `expire.sh`, `check.sh` (with `QD_REPO_LISTING=pgbackrest`, because its repository
> is a bucket and not a directory), `monthly-archive.sh`, `archive-heartbeat.sh` and `lib.sh`. The
> retention figures of `pgbackrest.conf` are the single host's too
> (`backend/tests/test_single_host_files.py` fails if they drift apart), and the timers' calendars are
> its scheduler's.

**What has been proven.** Only this: on a developer machine (Docker Desktop on Windows), in containers,
against the rehearsal stack of `deploy/rehearsal/`, the scripts take a full and a differential backup,
the restore test passes on a database built by the repository's migrations and fails when it should,
`check.sh` notices a stopped archive, and the file store scripts copy and archive a bucket. One machine
with containers proves the scripts, not two servers. Nothing here has been installed anywhere: no server
exists yet. See "Not proven" at the end.

## What is where

| Path | What it is |
|---|---|
| `pgbackrest.conf` | pgBackRest on the **standby**, which holds the repository: AES-256, zstd, the retention settings with the reasoning for each number |
| `pgbackrest-primary.conf` | pgBackRest on the **primary**: pushes WAL to the standby's repository over the private link |
| `postgresql-archive.conf` | `archive_mode`, `archive_command`, `archive_timeout = 60` for both database servers |
| `backup.env.example` | Every setting the scripts read, without secrets |
| `scripts/backup.sh <full\|diff>` | One backup; one JSON line |
| `scripts/restore-test.sh` | Restores the latest backup into a throwaway instance and checks it |
| `scripts/expire.sh` | pgBackRest expiry, and pruning of the file archives and monthly dumps |
| `scripts/check.sh` | Age of the newest backup and of the newest archived WAL segment; non-zero exit when too old |
| `scripts/monthly-archive.sh` | The monthly dump that gives "monthly for 12 months" |
| `scripts/filestore-sync.sh <sync\|archive>` | Copies the bucket to the standby's store; writes the weekly encrypted archive |
| `scripts/archive-heartbeat.sh` | One WAL record a minute on the primary, so that an idle database still archives |
| `systemd/` | One service and one timer per job |
| `proof/` | The local proof: a Compose overlay on the rehearsal stack and `run.sh` |

## What runs when and where

Times are Tashkent time (UTC+5), written into the timers with the time zone, so the server's own zone
does not matter. Everything runs as the `postgres` user.

| Timer | When | Where | What |
|---|---|---|---|
| `qd-backup-full` | Sunday 01:30 | standby | `backup.sh full` |
| `qd-backup-diff` | Monday to Saturday 01:30 | standby | `backup.sh diff` |
| `qd-filestore-archive` | Sunday 02:30 | standby | `filestore-sync.sh archive`: the bucket as one encrypted archive beside the repository |
| `qd-restore-test` | Wednesday 02:30 | standby | `restore-test.sh` |
| `qd-backup-expire` | daily 03:30 | standby | `expire.sh` |
| `qd-backup-monthly` | 1st of the month 04:30 | standby | `monthly-archive.sh` |
| `qd-backup-check` | every minute | standby | `check.sh --quiet`; writes the figures for monitoring |
| `qd-filestore-sync` | every 5 minutes | standby | `filestore-sync.sh sync` |
| `qd-archive-heartbeat` | every minute | primary (installed on both) | `archive-heartbeat.sh` |

WAL archiving itself is not a timer: PostgreSQL on the primary runs `archive_command` for every finished
segment, and `archive_timeout = 60` finishes a segment that holds anything after a minute at the latest.

Each job writes exactly one JSON line to the journal when it ends (`journalctl -u qd-backup-full.service -o cat`):
event, outcome, start and end, duration, backup label, sizes in bytes. Identifiers and numbers only; no
names, no passwords. Warnings and errors of the tools go to standard error, which the journal keeps too.
`check.sh --quiet` and `archive-heartbeat.sh` print nothing when all is well, because they run every minute.

## Retention: what the document asks and what this gives

| Operations document | How it is met |
|---|---|
| Write-ahead log archived every minute | `archive_timeout = 60` plus the heartbeat. Without the heartbeat an idle database archives nothing: measured locally, 0 segments in 200 seconds without writes |
| Weekly full, daily differential | The two timers above |
| Point-in-time recovery for 14 days | `repo1-retention-archive-type=full`, `repo1-retention-archive=3`: WAL is kept from the third-newest full backup on, so the window is between 14 and 21 days, never less than 14. pgBackRest counts this in backups, not days; exactly 14 days cannot be said |
| Weekly backups for 8 weeks | `repo1-retention-full-type=time`, `repo1-retention-full=56`: every full of the last 56 days is kept, plus one older (9 at most). Fulls older than the 14-day window restore to their own moment only |
| Monthly for 12 months | **Not by pgBackRest**, which keeps full backups by one rule per repository. `monthly-archive.sh` writes a logical dump (`pg_dump`, custom format, encrypted with the same passphrase) on the 1st of each month and `expire.sh` keeps twelve. A dump is one moment, restores without WAL, and takes longer to restore than a physical backup |
| Files included in weekly backup | `files-YYYYMMDD.tar.gz.enc` beside the repository; `expire.sh` keeps the newest 8 and the first of each of the last 12 months |
| Automated restore test weekly | `qd-restore-test.timer` |
| Full timed rehearsal quarterly | **Not covered.** It needs two servers and a person; `deploy/rehearsal/` is its local form |
| Location: standby, plus a third location | Standby only. **No third location exists**; until one does, losing both servers loses everything (the document says the same) |

## Install

Nothing below has been run on a server. Both servers: PostgreSQL 16, pgBackRest 2.50 or newer, `jq`.
The standby also needs `rclone`, `openssl`, `tar`, `gzip`, and node_exporter with the textfile collector.

1. **Private link and SSH.** The `postgres` user of each server must reach the other over the private
   link without a password (an SSH key pair per server, restricted to that address). pgBackRest uses it
   in both directions: the primary pushes WAL to the standby, the standby reads the primary for backups.
2. **PostgreSQL.** Copy `postgresql-archive.conf` to `/etc/postgresql/16/main/conf.d/20-archive.conf` on
   both servers; restart.
3. **pgBackRest.** On the standby copy `pgbackrest.conf`, on the primary `pgbackrest-primary.conf`, to
   `/etc/pgbackrest/pgbackrest.conf`; replace the two placeholder addresses. On the standby create
   `/etc/pgbackrest/conf.d/cipher.conf` (see "The key"). Then on the standby, as `postgres`:
   `pgbackrest --stanza=qarz stanza-create` and `pgbackrest --stanza=qarz check`.
4. **Scripts.** Copy `scripts/` to `/opt/qarz-backup/scripts/` on both servers (owner root, mode 0755).
   Copy `backup.env.example` to `/etc/qarz-backup/backup.env` and set `QD_MIGRATIONS_DIR` to the
   migrations of the checkout that is deployed. Create `/var/lib/qarz-backup` (owner `postgres`, mode
   0750) on the disk that holds the repository, and the textfile directory, writable by `postgres`.
5. **File store.** Write `/etc/qarz-backup/rclone.conf` (owner `postgres`, mode 0600) with two S3 remotes,
   `primary` and `standby`, each with its endpoint and a key that may read (primary) or write (standby)
   the bucket `qd-files`.
6. **Units.** Copy `systemd/*` to `/etc/systemd/system/`, `systemctl daemon-reload`, then on the standby
   `systemctl enable --now` every `qd-*.timer`, and on the primary only `qd-archive-heartbeat.timer`.
   (Enabling the heartbeat on the standby too is harmless and makes a promoted standby archive at once.)
7. **First backup and first test, by hand** (next section), before trusting the timers.
8. **Monitoring.** Point node_exporter at the textfile directory and load `deploy/monitoring/alerts.yml`.
   Trigger each alert once on purpose (launch criterion 9): stop the heartbeat and archiving for six
   minutes; run `restore-test.sh` with `QD_EXPECTED_MIGRATION_HEAD=wrong`.

## By hand

```bash
# as postgres, on the standby
/opt/qarz-backup/scripts/backup.sh full          # or: diff
/opt/qarz-backup/scripts/restore-test.sh         # exit 0 and "outcome":"ok", or the name of the failed check
/opt/qarz-backup/scripts/check.sh                # three lines with ages and limits; exit 1 when something is too old
/opt/qarz-backup/scripts/expire.sh --dry-run     # what would be removed
pgbackrest --stanza=qarz info                    # what the repository holds
```

Through systemd, with the journal line: `systemctl start qd-backup-diff.service`, then
`journalctl -u qd-backup-diff.service -n 5 -o cat`.

A point-in-time restore is runbook 3 (`docs/10-operations/runbooks.md`); it is done by a person, into a
new instance, and is not one of these scripts.

## What the restore test checks

It restores the newest backup, whatever its type, into a directory under `QD_RESTORE_TEST_DIR`, starts
PostgreSQL there on a Unix socket only (no network, archiving off), and stops at the first failure:

| Check | What fails it |
|---|---|
| `restore` | pgBackRest cannot restore (it verifies the checksum of every file), or too little disk |
| `start`, `consistent` | PostgreSQL does not start, or recovery does not reach a consistent state and end |
| `migration` | `alembic_version` differs from the head of the migrations in `QD_MIGRATIONS_DIR` |
| `rows` | A table of `QD_ROWCOUNT_TABLES` is empty in the restored copy and not in the database this host serves |
| `ledger` | `open_debt_mismatches(NULL)` returns rows, or a customer's balance over unreversed entries is negative (INV-3) |

Then it removes the instance and reports the seconds for the restore, the start and the whole test.
It recovers only as far as the backup needs to be consistent (`--type=immediate`); it does not replay
the archive to a later moment. Replay is what the point-in-time restore and the quarterly rehearsal test.

The operations document says "into staging". Here the throwaway instance is created on the standby,
which is also the staging host (ADR-014), beside the replica and without touching it; it is not the
staging application's database.

## The key

The repository, the file archives and the monthly dumps are encrypted with one passphrase (AES-256).

- Generate it once, away from both servers: `openssl rand -base64 48`.
- Keep it in **two places off both servers**: the operator's password manager and one more place that
  does not depend on the first (for example a sealed paper copy with the named second person).
- **Losing it loses every backup.** There is no recovery and no back door; the operations document lists
  "backup key lost" as prevention only.
- A working copy must also sit on the standby, in `/etc/pgbackrest/conf.d/cipher.conf` (owner `postgres`,
  mode 0600; two lines: `[global]` and `repo1-cipher-pass=<passphrase>`), because the nightly backups
  encrypt with it. So the encryption protects a copied or stolen repository, a third location and a
  discarded disk; it does not protect against someone who has taken over the standby itself. The
  primary does not need the passphrase.
- The passphrase is in no file of this repository, and the scripts never print it: they pass it to
  `openssl` through the environment of that one process.
- Rotation: runbook 4.

## What is not backed up

As the operations document says: logs; images (rebuilt from the repository); environment files and
secrets (the operator's password manager). Also not backed up: the pgBackRest and rclone configuration on
the servers (they are these files plus the secrets above), the monitoring system's own data, and the
passwords of database roles in the monthly dump (`--no-role-passwords`; the physical backups do hold them).

## Decisions taken where the documents are silent

- **The repository host runs the backups.** With the repository on the standby, pgBackRest requires the
  backup command to run there; so every timer but the heartbeat lives on the standby. If the standby is
  lost, nothing backs up until it is rebuilt (the document's "backups go to the third location" has no
  third location to go to).
- **SSH over the private link** between the two `postgres` users, rather than pgBackRest's TLS server:
  no certificates to issue and renew for two hosts.
- **Thresholds for "backup missing"**: no backup of any type for 26 hours, or no full backup for 8 days.
  The document gives only "failed or missing". The archive threshold, 5 minutes, is the document's.
- **A heartbeat write every minute**, because PostgreSQL does not close an untouched WAL segment and the
  archive alert goes to the operator's phone; without it the alert would fire every quiet night.
- **The restore test runs on Wednesday**, when the latest backup is a differential: restoring it reads
  the week's full backup too.
- **Expiry is its own job** (`expire-auto=n`), so that removal has its own line in the journal.
- **Files are copied every five minutes** with rclone; the document says "continuously". A file uploaded
  and lost within those minutes is lost. A deletion is copied too, but a run that would delete more than
  100 files stops with an error, so an emptied source cannot empty the copy.
- **The alert for a stale archive is `severity: phone`**, as the document's alerting table puts "archive
  stale" with the alerts that reach the phone; the other three go to the operator's chat.
- **The monthly dump is taken from the standby's replica**, to keep the load off the primary. A long dump
  on a replica can be cancelled by replay; `max_standby_streaming_delay` may need raising. Not measured.

## The local proof

```bash
bash deploy/backup/proof/run.sh all     # up, run, down; about four minutes, plus the image builds the first time
```

`up` starts the rehearsal stack under the Compose project `qd-backup-proof` (its own containers,
volumes, images and ports 55632, 55633, 55910, 55911), adds a second object store and a tools container,
and builds the application's schema in the rehearsal primary with the repository's migrations
(`python -m loadtest.load --profile tiny`, which runs `alembic upgrade head` and loads 4 generated shops).
It needs a Python with the backend installed (`pip install -e 'backend[dev]'`; `QD_PYTHON` names it).

`run` prints `PASS` and `FAIL` lines. It takes a full and a differential backup, runs the restore test,
and then proves each refusal: a wrong migration head, a stored open debt that differs from the ledger, a
customer with a negative balance, the newest backup's largest file overwritten with random bytes, a
stopped archive, backups older than the threshold, a source bucket emptied. Two thresholds are shortened
for the proof through the environment and the output says so: 20 seconds instead of 300 for the archive,
and 1 deletion instead of 100 for the file store.

In the proof `backup.sh` runs in the primary's container and the other scripts in the standby's, because
the rehearsal mounts one repository volume into every container instead of reaching it over a link.

The proof takes longer than three minutes with its image builds, so CI does not run it. CI runs
shellcheck on the scripts, `systemd-analyze verify` on the units, a syntax check of the Compose overlay,
and the alert rules' own tests (`promtool test rules deploy/monitoring/alerts.test.yml`).

## Not proven

- Anything on a real server, on two servers, or over a link between them: `pg2-host`, `repo1-host`, SSH,
  and a backup that the standby takes from the primary. Only the retention options of `pgbackrest.conf`
  were parsed and applied locally (`expire --dry-run`).
- The systemd units under systemd: they pass `systemd-analyze verify` and their calendar expressions
  parse, but no timer has fired.
- That retention behaves as described over weeks. The settings are read from pgBackRest's documentation;
  no repository here is older than a few minutes.
- Backup and restore times. The proof's database is 45 MB: a full backup took 4 to 6 seconds and a
  restore test about 8. That says nothing about a production database.
- node_exporter reading the textfile directory, a monitoring system loading the rules, and an alert
  reaching anybody.
- Garage as the standby's store in a real layout, replication of a bucket larger than three small files,
  and restoring the file store from a weekly archive (the archive is only read back and counted).
- Restoring from a monthly dump: the dump is decrypted and its table of contents is read, no more.
- The restore test against a replica as the source of the row counts under replay lag, and after a
  release that carries a migration (deployed between the night's backup and the test, it would fail the
  `migration` check once).
- What happens to backups after a failover. pgBackRest is expected to find the new primary by itself
  (`pg1` and `pg2` in `pgbackrest.conf`); this was not tried.
- The quarterly timed rehearsal and the third location: neither exists.
