# Local rehearsal of the two-server database design

A Docker Compose stack that rehearses, on one machine, the database part of story S1.5: a PostgreSQL 16 primary, an asynchronous streaming standby, continuous WAL archiving and base backups with pgBackRest, manual scripted failover, point-in-time restore, and an S3-compatible file store. It implements the decisions on two servers with manual failover (ADR-014), on replication and archiving (ADR-015), and on the file store (ADR-020) as far as one machine can.

This is not a deployment. Nothing here orders, pays for, or touches a real server. All data and passwords are throwaway.

Measured results of the first rehearsal: `docs/10-operations/rehearsals/2026-10-06-local.md`.

## What is in the stack

| Service | Role | Host port (loopback only) |
|---|---|---|
| `pg-primary` | PostgreSQL 16 primary; archives WAL with `archive_timeout = 60` seconds | 55532 |
| `pg-standby` | Streaming standby, asynchronous; built by restoring the first base backup | 55533 |
| `pg-pitr` | Started only by `pitr.sh`; a fresh instance restored from the backup repository | 55534 |
| `filestore` | Garage, an S3-compatible object store; one node, bucket `qd-files` | 55900 |

Volumes: one data volume per database, one shared pgBackRest repository (`backup-repo`, encrypted with AES-256), one small `fence` volume, two for the file store.

Ports can be changed with `QD_PORT_PRIMARY`, `QD_PORT_STANDBY`, `QD_PORT_PITR`, and `QD_PORT_FILESTORE`. The defaults avoid 54329 (the development database). On Windows a port can fall inside a range reserved by the system (`netsh interface ipv4 show excludedportrange protocol=tcp`); Docker then reports "An attempt was made to access a socket in a way forbidden by its access permissions". Pick another port in that case.

## Requirements

Docker with Compose v2, bash, GNU `date`, `awk`, `od`, and `curl` 7.75 or newer (for the signed file store requests). Git Bash on Windows with Docker Desktop provides all of them. About 1 GB of disk for images.

## How to run

From the repository root:

```bash
bash deploy/rehearsal/scripts/up.sh        # about 30 seconds after the first image build
bash deploy/rehearsal/scripts/verify.sh    # about 3 minutes; kills the primary on purpose
bash deploy/rehearsal/scripts/down.sh      # removes everything
```

`verify.sh` needs a fresh stack every time, because it ends with the standby promoted and the old primary fenced. Run `down.sh` and `up.sh` between runs.

## What each script does and proves

| Script | What it does | What it proves |
|---|---|---|
| `up.sh` | Generates throwaway secrets into `deploy/rehearsal/.env` (git-ignored), builds the images, starts the primary, creates the pgBackRest stanza, takes a full base backup, starts the standby by restoring that backup, waits until it streams, and creates a bucket and a key in the file store | A standby can be built from the backup repository alone, and streaming replication starts |
| `failover.sh` | Refuses if the standby is already promoted (exit 4) or if the primary still answers (exit 3). Otherwise waits for the standby to replay what it received, writes the fence file, promotes, and inserts a row. Prints the elapsed seconds | Promotion works and the promoted node accepts writes; the script does not create a second primary next to a live one |
| `pitr.sh '<timestamp>'` | Restores the backup repository into a new instance up to the given moment. Refuses a target that is not after the first base backup or is in the future (exit 5). Prints the elapsed seconds | Damage can be undone into a separate database without touching the live ones |
| `pitr.sh --archive-end --timeline 1` | Restores everything the archive holds for that timeline | How much survives if both database servers are lost and only the repository is left |
| `verify.sh` | Runs all checks below in order and prints `PASS`, `FAIL`, and `MEASURE` lines; saves the output to `deploy/rehearsal/.out/` | See the list below |
| `down.sh` | Removes the containers, volumes, network, and locally built images of the Compose project `qd-rehearsal`, and the generated `.env`. `--all` also removes the result logs. Base images pulled from registries stay in the local image cache | Nothing is left running |

Checks in `verify.sh`:

1. **Replication.** Five rows written on the primary become visible on the standby; reports the delay in milliseconds, measured inside the standby by a session that polls every millisecond.
2. **Standby is read-only (negative).** An insert on the standby must fail.
3. **Failover refused while the primary is alive (negative).** `failover.sh` must exit 3 and leave the standby a standby.
4. **Point-in-time restore.** Marker rows are written, a target time is taken, the rows are deleted and later rows written. After the restore the deleted rows must be present and the later rows absent.
5. **Restore before the first backup (negative).** Refused by the script's guard, and, with `--no-guard`, by pgBackRest itself.
6. **Loss bound.** A client writes one row a second and records each acknowledged row. The primary is killed with `docker kill`. Reports the age of the newest archived WAL segment at that moment.
7. **Failover.** Reports seconds from the kill to the first accepted write on the promoted standby, and how many acknowledged rows are missing there.
8. **Loss bound from the archive alone.** Restores only what was archived before the kill and reports how many acknowledged rows are missing.
9. **Old primary restarted (negative).** The old primary container must refuse to start; a second `failover.sh` must exit 4.
10. **File store.** An object is stored and read back with signed requests; an unsigned read must be refused.

`QD_WRITER_SECONDS` (default 75) sets how long the writer runs before the kill. Changing it moves the kill to a different point of the 60-second archiving cycle.

## Split brain: what the tooling does

- `failover.sh` probes the primary three times from the standby and refuses to promote while it answers.
- Before promoting, `failover.sh` writes the promoted node's name into a file on the shared `fence` volume. The database entrypoint (`postgres/qd-entrypoint.sh`) reads it on every start and exits with code 78 if another node was promoted.
- Without the fence the old primary starts as a normal writable database on the old timeline. It accepts writes that the promoted standby never sees, and pgBackRest accepts its WAL into the shared archive. This was observed once by setting `QD_IGNORE_FENCE=1`; the variable exists only for that observation.

## Limits of a local rehearsal

- One machine, one disk, one kernel, one clock, one Docker network. The numbers say nothing about the link between two providers.
- The database holds three small tables. Restore and backup times do not scale to a production database.
- The backup repository is a volume mounted into every database container. In the design it lives on the standby server and is reached over the private tunnel. The passphrase sits in a local file here; the design keeps the key off both servers.
- The fence file works only because the containers share a volume. Two real servers share nothing; fencing there needs its own step in the failover runbook.
- A dead container and a broken link look the same to the probe in `failover.sh`. The script cannot tell them apart; the operator must.
- `failover.sh` promotes the database and nothing else. Starting the API and worker on the standby, repointing DNS and the Telegram webhook, and rebuilding a standby are not rehearsed.
- The file store is one node. Replication of files to the standby is not rehearsed. Garage was used because the MinIO community images could not be pulled on 2026-10-06; this does not choose the production file store.
- Only one full backup is taken here. The weekly and daily backups, the restore test, expiry and the copy of the file store are in `deploy/backup/`, whose proof (`deploy/backup/proof/run.sh`) runs this stack under its own Compose project through `QD_COMPOSE_PROJECT` and `QD_COMPOSE_OVERLAY` (read by `scripts/lib.sh`). Retention over weeks is not rehearsed anywhere.
- Scripts were run from Git Bash on Windows 11 with Docker Desktop, and from a Linux bash inside a container that talked to the same Docker engine. They were not run on a native Linux host.
