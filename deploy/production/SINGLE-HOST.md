# The single-host deployment

The whole service on one computer, reached through a Cloudflare Tunnel, with its backups in a Cloudflare
R2 bucket. This is the **current deployment** by the founder's decision of 2026-10-08 (DEC-070): there is
no budget for servers, so the service runs on a Windows 11 machine with Docker Desktop that he already
has, behind NAT, started by hand when it goes down. The two-server design (`README.md` in this directory,
`deploy/backup/`, `deploy/rehearsal/`) stays in the repository as the design for when there are two
servers.

**What has been proven.** Only this: in containers on a developer machine and on the CI runner, with a
MinIO server in the place of R2 and a stand-in in the place of the tunnel, the stack comes up with one
command, the proxy's own smoke test passes from the tunnel's place, backups are taken on the schedule
and by command, and after the database volume is destroyed the data comes back from the bucket alone
(`scripts/single-host-proof.sh`, CI job `single-host`). **Nothing here has run as a service on the real
machine, through a real tunnel, or against a real R2 bucket.** See "Not proven" at the end.

## What it is

```
visitor ── HTTPS ──> Cloudflare ══ tunnel (outbound from this machine) ══> cloudflared
                                                                              │ plain HTTP, private network
                                                                              ▼
                                                    proxy (nginx) ──> api ──> db (PostgreSQL 16)
                                                                      worker ──┘      │ WAL, every minute
                                                    files volume <── api, worker       ▼
                                                         │ every 5 minutes        Cloudflare R2 bucket
                                                         └── files-backup ──────> (everything encrypted
                                                    backup (the schedule) ──────>  on this machine first)
```

| Path | What it is |
|---|---|
| `compose.single-host.yml` | Overlay on `compose.yml`: `db`, `cloudflared`, `backup`, `files-backup`, the one-shots `owner` and `roles`, and `restore`, `restore-files`, `db-scratch` for use by hand. Takes the proxy's published ports and certificate away. |
| `single-host.env.example` | The settings it adds, without values |
| `scripts/single-host.sh` | Every command of this document |
| `single-host/postgres/` | The database image: PostgreSQL 16.15, pgBackRest 2.59, rclone; `pgbackrest.conf` |
| `single-host/scripts/` | What runs inside that image: the scheduler, the jobs, the copy of the files, restore, checks |
| `nginx/nginx.single-host.conf`, `nginx/single-host.d/`, `nginx/single-host-entrypoint.sh` | The proxy's form behind the tunnel; the limits and routes are the same files as the other form's |
| `compose.single-host.proof.yml`, `scripts/single-host-proof.sh`, `scripts/single_host_static.py` | The proof |

Nothing in the composition publishes a port on the host and nothing of the host's file system is
mounted: the way in is the tunnel, and every piece of state is a Docker volume
(`pgdata`, `files`, `backup-state`, `files-state`, `pgsocket`).

## First deployment: the founder's checklist

In this order. **You** marks what only the founder can do; **script** what a command does.

### 1. Decide (you)

1. The public host name: a subdomain of a domain that is already in your Cloudflare account, for
   example `qarz.<your domain>`. It goes into the env file as `DEPLOY_PUBLIC_HOST` and into Cloudflare
   in step 3. Changing it later means BotFather and the webhook again (step 7).
2. Where the backup passphrase will be kept: two places that are not this machine (step 5).
3. Read "What you should know before relying on it" below. Two of its points need your decision before
   real customer data is recorded: personal data leaves Uzbekistan in this design, and there is no
   failover.

### 2. Prepare the machine (you)

Plain instructions; nothing in this repository changes the host.

- **Docker Desktop must be running for the service to run.** The containers come back by themselves
  whenever Docker starts (`restart: unless-stopped`), but Docker Desktop starts only after somebody signs
  in to Windows. Choose one:
  - *Sign in by hand after every reboot.* Nothing to set up; this is "started by hand". After a power
    cut at night the service is down until you sign in. In Docker Desktop, Settings, General: turn on
    "Start Docker Desktop when you sign in to your computer".
  - *Automatic sign-in of a dedicated Windows account* (Sysinternals Autologon, or `netplwiz`), with the
    Docker Desktop setting above. The machine then comes back from a reboot alone. The cost: the
    account's password is stored by Windows, and whoever can touch the machine is signed in. Use an
    account that has nothing else, and lock the screen at once (a shortcut to
    `rundll32.exe user32.dll,LockWorkStation` in the account's Startup folder).
  - *Docker Engine inside a WSL 2 distribution, started at boot without a sign-in.* More robust and
    more work; not tried here, and it would replace Docker Desktop for every project on the machine.
- **Power.** In the BIOS or UEFI setup set "restore on AC power loss" (also "AC back", "after power
  failure") to *Power On*, so that the machine starts when the electricity returns. Put the machine
  **and the router** on a UPS.
- **Never sleep.** Windows, Settings, System, Power: sleep "Never" when plugged in; turn hibernation and
  fast start-up off. A sleeping machine is a service that is down, and Docker's clock drifts after
  sleep, which breaks sign-in (Telegram's signed data and the administrators' codes are checked against
  the clock). Keep "Set time automatically" on.
- **Windows Update must not reboot in shop hours.** Settings, Windows Update, Advanced options: set the
  active hours to 06:00 to 23:00 (the longest span Windows allows is 18 hours) and turn on "Notify me
  when a restart is required". Install updates and reboot yourself at night; after the reboot check
  `single-host.sh status`. Docker Desktop: turn off automatic updates of itself, or update it at night
  too; an update restarts every container.
- **Encrypt the disk** with BitLocker (Windows 11 Pro; with the TPM it unlocks by itself at start, so a
  reboot still needs nobody). Without it, whoever takes the machine has the database, the stored
  receipts and the env file. Keep the BitLocker recovery key off the machine.
- **Disk space.** The database, the files and the write-ahead log live in Docker's virtual disk. When
  it fills, PostgreSQL stops. Give it room in Docker Desktop, Settings, Resources; look at the line
  "disk" of `single-host.sh status` every week; let Windows warn you (Storage Sense notifications). The
  logs of this project are capped (20 MB a file, 5 or 10 files a service) and the write-ahead log that
  waits for an unreachable bucket is capped at 4 GiB.
- **If other projects share Docker on the machine:** the memory limits of this project add up to
  3.3 GiB; they do not limit anything else. And on this machine **never** run `docker system prune
  --volumes`, `docker volume prune`, `docker compose down -v` for the project `qarz`, Docker Desktop's
  "Clean / Purge data" or "Reset to factory defaults", and never unregister its WSL distribution: the
  database is a Docker volume, and each of these can destroy it. If one of them must be done, read
  "Move the service to another machine" in the runbooks first, because that is what it then is.
- **Network.** No port has to be opened and no address has to be public. The machine must reach
  Cloudflare outbound on TCP 443 and on 7844 (UDP for QUIC, TCP as the fallback), and Telegram on 443.

### 3. Cloudflare (you)

Nothing in this repository creates or changes anything in Cloudflare.

1. **The bucket.** R2, Create bucket. One bucket for this service, with a name of its own. Choose its
   location knowing that it is outside Uzbekistan wherever you put it.
2. **A key limited to that bucket.** R2, Manage API tokens, Create: permission *Object Read & Write*,
   *Apply to specific buckets only* and that one bucket. Copy the **Access Key ID**, the **Secret Access
   Key** and the S3 endpoint host (`<account id>.r2.cloudflarestorage.com`) into your password manager.
   The key cannot list or create buckets, and does not need to.
3. **The tunnel.** Zero Trust, Networks, Tunnels, Create a tunnel, type *Cloudflared*, give it a name.
   Cloudflare shows an install command that contains the **token** (the long string after `--token`).
   Copy only the token into your password manager; run none of the commands shown: the tunnel runs in
   this project's container, not as a Windows service. Do not touch the tunnels this machine already
   has.
4. **The route.** In that tunnel, Public Hostname, Add: your subdomain and domain; Service type `HTTP`,
   URL `proxy:8080`. Nothing else. This is what points the name at the proxy inside the Compose
   network; Cloudflare creates the DNS record itself.
5. **The zone's settings** for that host name:
   - SSL/TLS, Edge Certificates: **Always Use HTTPS** on. (The proxy also redirects a visitor who
     reached Cloudflare over plain HTTP, so this is the second lock, not the only one.)
   - Speed: **Rocket Loader** off; Scrape Shield: **Email Address Obfuscation** off. Both rewrite the
     pages, and the pages' Content-Security-Policy refuses what they inject.
   - Do not add Cloudflare's own HSTS setting with different values; the proxy sends
     `Strict-Transport-Security: max-age=31536000` itself.
   - Security: **Bot Fight Mode** and **Browser Integrity Check** can refuse callers that are not
     browsers. Telegram's webhook calls and the payment providers' calls are exactly that. Leave both
     off for this host name, or add a rule that skips them for `/tg/webhook` and `/pay/`, and watch the
     webhook after any change (step 8). Not tried here.

### 4. The env file (you run the command; the script generates)

```sh
deploy/production/scripts/single-host.sh env-init        # writes ~/.qarz/single-host.env, mode 600
```

**Script:** generates the backup passphrase, the four database passwords and their connection strings,
the webhook secret, the server secret and the metrics token, and writes them into the file. It prints
none of them and refuses to overwrite an existing file.

**You:** open the file and fill in the names at its top:

| Name | From |
|---|---|
| `DEPLOY_PUBLIC_HOST` | step 1 |
| `CLOUDFLARE_TUNNEL_TOKEN` | step 3.3 |
| `DEPLOY_R2_ENDPOINT`, `DEPLOY_R2_BUCKET`, `DEPLOY_R2_ACCESS_KEY_ID`, `DEPLOY_R2_SECRET_ACCESS_KEY` | steps 3.1 and 3.2 |
| `VITE_BOT_USERNAME`, `QD_BOT_TOKEN` | BotFather: the production bot's username without `@`, and its token |
| `QD_ADMIN_TG_IDS` | the numeric Telegram identifiers of the administrators, separated by commas |

A value that contains `$` goes in single quotes. The file is never committed, never pasted into a chat,
and never printed by the scripts.

### 5. The passphrase and the env file, off this machine (you)

- Copy `DEPLOY_BACKUP_PASSPHRASE` to **two places that are not this machine**: your password manager
  and one more place that does not depend on the first (a sealed paper copy with the named second
  person, for example). **Without it the backups in the bucket cannot be read by anybody, you
  included.** There is no recovery and no back door. The copy in the env file exists only so that the
  nightly backup can encrypt; it is lost together with the machine.
- Put a copy of the whole env file into the password manager as well. Moving to another machine needs
  the passphrase, the R2 key, the tunnel token, the bot token, `QD_SECRETS_KEY` (without it the
  administrators must enrol their second factor again) and `QD_WEBHOOK_SECRET`.

### 6. Start (script)

```sh
git -C <checkout> pull                      # the checkout must be at the commit to run
deploy/production/scripts/single-host.sh up
```

**Script, in this order:** checks that every required name has a value; builds the three images from
the commit; checks the data volume against the bucket and stops if the volume is empty while the bucket
holds backups (that is a new machine: restore first) or if the bucket cannot be read; starts
PostgreSQL; creates the backup repository in the bucket; sets the owner's password; runs the
migrations; sets the three roles' passwords from their connection strings; starts the worker, the API
and the proxy and waits for `/healthz`; starts the backups and the tunnel. The same command deploys a
later release. The plain Compose equivalent of "start everything that is deployed" is
`single-host.sh start`.

Within a minute, without being asked, the scheduler takes the first full backup, runs the first
restore test, and writes the first monthly dump.

### 7. Telegram (you)

Nothing in this repository calls Telegram.

1. BotFather, `/setdomain` for the bot: the public host name. The Login widget of `/panel/` and
   `/admin/` works for that domain only.
2. BotFather, the bot's Mini App (menu button) address: `https://<host>/app/`.
3. The webhook. In a terminal on the machine, so that the token is in no chat and no history:

   ```sh
   ( set -a; . ~/.qarz/single-host.env; set +a
     curl -sS "https://api.telegram.org/bot${QD_BOT_TOKEN}/setWebhook" \
       --data-urlencode "url=https://${DEPLOY_PUBLIC_HOST}/tg/webhook" \
       --data-urlencode "secret_token=${QD_WEBHOOK_SECRET}" )
   ```

   Telegram answers `{"ok":true,...}`. `getWebhookInfo` in the same way shows the last error, if any.

### 8. Check (you run; the scripts check)

```sh
deploy/production/scripts/single-host.sh status       # everything Up; backups and WAL fresh; files copied
deploy/production/scripts/single-host.sh smoke        # smoke.sh against https://$DEPLOY_PUBLIC_HOST
deploy/production/scripts/single-host.sh restore-test # "outcome":"ok"
```

- `docker compose -p qarz logs proxy | tail`: `remote_addr` must be visitors' own addresses (yours, when
  you open the site from a phone on mobile data) and `peer` the tunnel's (`10.99.240.2`). If
  `remote_addr` is `10.99.240.2` for everybody, the proxy is not being told the visitor's address and the
  limits by address count all visitors as one: stop and find out why before going on.
- In the R2 dashboard the bucket holds two folders, `pgbackrest` and `crypt`, and no readable name.
- Send `/start` to the bot; open `/panel/` and sign in; open the Mini App in Telegram.
- Optional, and worth it (see "What is watched"): an outside check of `https://<host>/healthz`, and
  Cloudflare's notification for the tunnel.

## Day to day

```sh
single-host.sh status                     # what runs; ages of the newest backup and WAL segment; disk
single-host.sh up [<git-ref>]             # a new release (default: the checkout's HEAD)
single-host.sh rollback <previous-ref>    # the previous images; the schema is not changed
single-host.sh backup full|diff           # one backup, now
single-host.sh restore-test               # restore the latest backup into a throwaway instance, check it
single-host.sh pitr '<moment>'            # a copy of the database as it was, beside the live one
single-host.sh pitr-down
single-host.sh logs [<service>...]
single-host.sh stop | start
```

After a reboot nothing has to be run: once Docker Desktop is up the containers start by themselves, the
tunnel reconnects, and the scheduler takes whatever backup was missed while the machine was off.

When something is wrong, the runbooks are in `docs/10-operations/runbooks.md`; each of 1 to 5 has a part
"On the single host":

| What happened | Runbook |
|---|---|
| The site or the bot does not answer | 5, then 14 |
| The computer was off, lost power, restarted; Docker is not running | 14 |
| The machine or its disk is lost; Docker's data was wiped; moving to another machine | 15 |
| Data was damaged and an earlier state is needed | 3 |
| A secret leaked or must be changed: bot token, tunnel token, R2 key, database passwords, the passphrase | 4 |
| A new release, or taking one back | 1 |

## The proxy behind the tunnel

Every request reaches nginx from one address, the tunnel container's. What nginx believes, and from
whom (`nginx/single-host.d/qarz.conf`, `nginx/single-host-entrypoint.sh`):

- **The visitor's address** is Cloudflare's `CF-Connecting-IP` header, and only when the connection
  comes from the tunnel container's fixed address on the private network the two share
  (`set_real_ip_from <that one address>`; never a network, the start script refuses anything but an
  address). From any other peer the header is ignored and the peer's own address counts. The limits by
  address (`limit_req`, `limit_conn`: all six zones, `qd_static` included), the access log
  (`remote_addr`; `peer` is where the connection came from) and the `X-Forwarded-For` given to the API
  all use that one address.
- **The visitor's scheme** is Cloudflare's `X-Forwarded-Proto`, on the same condition. A request not
  known to have reached Cloudflare over HTTPS is answered with a redirect to HTTPS and nothing else. So
  every page and every API answer is given to an HTTPS visitor: `Strict-Transport-Security` and the
  other security headers are true as the browser sees them, the API is told `https` and its own
  redirects name `https://`, and the session cookies are `Secure` (they always are, in the code).
- **Between the tunnel and nginx: plain HTTP**, on a Compose network that is `internal` and holds those
  two containers and nobody else, with no port published. TLS on that hop would protect against a
  reader who is already root on this machine, and such a reader also holds the certificate's key; it
  would add a certificate to make, mount and renew for no gain. The visitor's TLS ends at Cloudflare.
- **The application** has no notion of a client address of its own: its rate limits are per signed-in
  user and per shop, and its log and security events carry identifiers only. What it is handed
  (`X-Forwarded-For`, read by uvicorn) is the same address nginx settled on.

## Backups

| What | How | Where in the bucket | Encrypted |
|---|---|---|---|
| The database: full backup Sunday 01:30, differential Monday to Saturday 01:30 (Tashkent time) | pgBackRest | `pgbackrest/` | AES-256-CBC, by pgBackRest, with the passphrase, before sending |
| The write-ahead log: every finished segment, and a segment is finished after 60 seconds at the latest (a heartbeat writes one record a minute so that a quiet night still archives) | pgBackRest `archive-push`, from the database container | `pgbackrest/` | the same |
| The stored files (receipts, imports, exports): every 5 minutes | `rclone sync` through a `crypt` remote, from the `files` volume | `crypt/` | contents, file names and directory names, by rclone, with the same passphrase |
| A monthly logical dump, 1st of the month 04:30 | `pg_dump`, then `rclone` | `crypt/` | twice: openssl AES-256 with the passphrase, then rclone |

**Why the files are synced and not written to R2 by the application.** The application has an S3
adapter and could use an R2 bucket directly, but then every receipt (a photograph of a card transfer)
would be stored abroad readable by whoever holds the bucket. Kept in a volume here and copied through
`crypt`, nothing readable leaves the machine. The price: a file uploaded less than five minutes before
the disk is lost is lost, and the application runs on its filesystem store, which the code describes as
"for development and tests" (it is the same interface, covered by the same tests; it has not run under
real load).

A file deleted or replaced here is not deleted in the bucket at once: it is moved aside there and
deleted 30 days later. An empty directory is never synchronised over a copy that holds files, and a run
that would remove more than 100 files stops.

**The schedule runs in a container** (`backup`, `single-host/scripts/scheduler.sh`): this host has no
systemd. A job is due when its latest scheduled moment has passed and it has not run since, so a backup
missed because the machine was off is taken when it comes back. Every job writes one JSON line to the
container's log (`single-host.sh logs backup`); the same figures the two-server scripts write for
monitoring are files in the `backup-state` volume (`qd_backup_last_success_timestamp_seconds{type}`,
`qd_wal_archive_newest_age_seconds`, `qd_backup_restore_test_last_success_timestamp_seconds`, ...). The
`backup` container is **healthy** in `docker ps` only while the newest backup is younger than 26 hours,
the newest full younger than 8 days and the newest archived WAL segment younger than 5 minutes.

**The restore test** (Wednesday 02:30, and `single-host.sh restore-test`) restores the latest backup
from the bucket into a throwaway PostgreSQL inside the `backup` container, which has the live data
volume mounted read-only, and checks: pgBackRest's checksums, that PostgreSQL starts and reaches a
consistent state, that the migration is the live database's, that no table that has rows is empty, and
that the ledger agrees with itself (`open_debt_mismatches`, no negative balance).

### Retention: DEC-060's figures, and what one repository changes

| DEC-060 / operations document | On the single host |
|---|---|
| Weekly full, daily differential | The same |
| Point-in-time recovery for 14 to 21 days | The same settings (`repo1-retention-archive=3`, by full backups) |
| Weekly backups for 8 weeks | The same (`repo1-retention-full=56` days) |
| Monthly for 12 months, as encrypted dumps | The same, copied to the bucket; the newest 12 are kept there |
| Files copied every five minutes | The same interval; the copy is now encrypted, and deletions are delayed 30 days |
| Files included in the weekly backup as one archive, 8 weeks and 12 months kept | **Not done.** There is one copy of the files (current, plus what was removed in the last 30 days), not dated archives: a file damaged more than 30 days ago cannot be had back |
| The repository on the standby, plus a third location | **One location: the bucket.** There is no standby and no third location |
| The key held off both servers | A working copy is on the machine (it must be, to encrypt); the real copies are the two off it |
| Restore test weekly "into staging" | Weekly, into a throwaway instance on the same machine |
| A stale archive reaches the operator's phone | **Nobody is told.** See "What is watched" |

### What can be lost, and how long recovery takes

- **The machine is switched off, loses power, or Windows reboots:** nothing is lost. A recorded entry is
  on the disk before the seller is answered. The service is down until the machine is up and Docker
  runs.
- **The machine or its disk is lost:** what the bucket holds can be restored. The write-ahead log is
  sent within 60 seconds of a write, plus the seconds the upload takes, **while the machine can reach
  R2**. So the loss is normally the last one to two minutes of entries; if the internet link was down
  when the disk died, it is everything since the link went down. Files: up to five minutes.
- **Recovery time** is the time to have another machine with Docker, plus the restore, plus starting.
  The restore itself took seconds in the proof on a 34 MB database and says nothing about a real one.
  No number is promised.
- **The passphrase is lost:** everything in the bucket is lost with it.

## What is watched, and what is not

Honestly: almost nothing is watched by anything but a person.

| Signal | State on the single host |
|---|---|
| A process dies | Docker restarts it (`restart: unless-stopped`) |
| A container is unhealthy but running | Shown by `docker ps` and `single-host.sh status`. **Docker does not restart it and nobody is told** |
| Backups or the WAL archive are stale | The `backup` container turns unhealthy; one line a check in its log. **Nobody is told** |
| The copy of the files fails | The `files-backup` container turns unhealthy when no copy has succeeded for a quarter of an hour; a `"outcome":"failed"` line in its log. **Nobody is told** |
| Disk filling | `single-host.sh status` prints it. **Nobody is told** |
| The whole machine is off, or the internet link is down | **Nothing on the machine can tell anybody.** Only something outside can |
| Error rates, latency, the outbox, security events (`deploy/monitoring/alerts.yml`) | The API serves `/metrics` inside the Compose network; **nothing reads it** |

Two things outside the machine cost nothing and are worth setting up. Both are yours to create; nothing
here does it.

- **Cloudflare's tunnel notification.** Cloudflare dashboard, Notifications, Add: *Tunnel Health
  Alert*, for this tunnel, to your e-mail. Cloudflare then tells you when the tunnel stops being
  connected, which is what "the machine is off or offline" looks like from outside.
- **An outside check of `https://<host>/healthz`** every minute by any uptime service that can notify a
  phone. `/healthz` answers `ok` only when the tunnel, the proxy, the API and the database all work. It
  says nothing about backups.

## What you should know before relying on it

1. **Personal data leaves Uzbekistan.** REQ-N04 says all personal data is stored on servers in
   Uzbekistan. Here the database and the files stay on a machine in Uzbekistan, but (a) every request
   and answer passes through Cloudflare, where the visitor's TLS ends, in the clear inside Cloudflare's
   network; and (b) the backups are stored in R2 abroad, encrypted with a key Cloudflare does not have.
   Whether either is compatible with the localization rule is a question for a lawyer, like the one
   already open about Telegram.
2. **There is no failover.** One machine, one disk, one power line, one internet link, one person. When
   any of them is down the service is down until that person acts.
3. **Cloudflare is now part of the service.** If Cloudflare, the tunnel or the account is unavailable
   or suspended, the service is unreachable although the machine is healthy. The tunnel token is a
   secret of the same weight as the bot token.
4. **The only second copy of anything is the bucket**, and it is only as good as the last successful
   upload and as the passphrase's safekeeping.
5. **Windows is the host.** Reboots for updates, sleep, the sign-in Docker Desktop needs, and whatever
   else uses the same Docker are all ways for the service to stop that a server does not have.

## Not proven

- Anything on the real machine: Docker Desktop on Windows as the place a service lives for months,
  restarts after reboots and power cuts, behaviour beside whatever else uses the same Docker, the memory
  limits under load.
- A real Cloudflare Tunnel: that Cloudflare sends `CF-Connecting-IP` and `X-Forwarded-Proto` as the
  proxy expects (written from Cloudflare's documentation), the tunnel's health check
  (`cloudflared tunnel ready`), reconnection after a link failure, and the zone settings of step 3.5.
- A real R2 bucket: pgBackRest and rclone were run against MinIO. The settings for R2 (path-style
  addressing, region `auto`, a key limited to one bucket) are from their documentation.
- Upload times over the machine's real link, the size of a real backup, and what R2 charges; expiry over
  weeks (no repository here is older than minutes).
- Telegram's webhook arriving through Cloudflare, and the Login widget on the real host name.
- `smoke.sh` against the public address. Through Cloudflare it has never run: Cloudflare normalises
  paths, may answer a plain-HTTP request or an oversized one itself before the proxy sees it, and adds
  headers of its own. A check that fails there for such a reason is to be understood and then decided
  on, not silenced; the same checks pass from the tunnel's place in the proof.
- The filesystem file store under real use.
- A restore onto a different machine by a person following runbook 15, timed.
