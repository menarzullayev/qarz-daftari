# Production deployment: nginx and Docker Compose

The files that put HisoBox (formerly Qarz Daftari) on the application host: one image for the API, the worker and the
migrations, one image for the proxy with the three front-end entries, a compose file, and scripts to
deploy, roll back and check.

> **Two ways to run it; one is current.**
>
> - **The current deployment is one machine**, by the founder's decision of 2026-10-08 (DEC-070): this
>   directory's `compose.yml` plus `compose.single-host.yml`, which adds PostgreSQL, a Cloudflare Tunnel
>   and backups to Cloudflare R2. Its guide, with the founder's checklist for the first deployment, is
>   **[SINGLE-HOST.md](SINGLE-HOST.md)**. Start there.
> - **The rest of this file describes `compose.yml` on its own: the application host of the two-server
>   design** (ADR-014), with a certificate, published ports 80 and 443, and PostgreSQL and the file store
>   on separate database hosts. No such servers exist. It is kept as the design for when there are two
>   servers, and because the local proof (`scripts/local.sh`) and the end-to-end suite (`e2e/`) run this
>   form. Where a section below says "the host", "a server" or "the database hosts", it means that
>   design, not the machine the service runs on today.
>
> Both forms use the same two images, the same `deploy.sh`, `rollback.sh` and `smoke.sh`, and the same
> nginx limits, routes and headers (`nginx/snippets/`); they differ in what stands in front of nginx and
> in where the database is.

**What has been proven.** Only this: on a developer machine (Docker Desktop on Windows), with a
self-signed certificate, a throwaway PostgreSQL and a directory in place of the file store, the stack
comes up through `deploy.sh`, `smoke.sh` passes, `rollback.sh` brings the previous images back, and the
three pages render in a browser under the content security policy. Nothing here has run on a server, with
a real certificate, a real bot or a real database host. See "Not proven" at the end.

## What is where

| Path | What it is |
|---|---|
| `backend/Dockerfile` | Python 3.12 slim, two stages. Installs the runtime part of `requirements.lock` with `--require-hashes` (selected by `backend/scripts/runtime_requirements.py`); no pip, compiler, tests or development tools in the final image; runs as user 10001. Default command is the API, with a health check on `/healthz`. |
| `nginx/Dockerfile` | Builds the three entries with `npm ci` and `npm run build`, then copies them and the configuration into `nginx:1.30-alpine`. `nginx -t` runs during the build. Runs as user 101 on ports 8080 and 8443. Build context is the repository root. |
| `nginx/nginx.conf`, `nginx/conf.d/qarz.conf` | The form that ends TLS itself: certificate, redirect from port 80. |
| `nginx/snippets/` | Shared by both forms: the limits by address (`limit-zones.conf`), the routes, body limits and error answers (`app-server.conf`), the headers, the access log (`http-common.conf`). |
| `nginx/nginx.single-host.conf`, `nginx/single-host.d/`, `nginx/single-host-entrypoint.sh` | The form behind a Cloudflare Tunnel ([SINGLE-HOST.md](SINGLE-HOST.md)). In the image, unused unless `compose.single-host.yml` starts it. |
| `compose.yml` | `proxy`, `api`, `worker`, `migrate`. Only the proxy publishes ports. |
| `compose.single-host.yml`, `single-host.env.example`, `single-host/`, `scripts/single-host.sh` | The single-host deployment ([SINGLE-HOST.md](SINGLE-HOST.md)). |
| `compose.local.yml` | Overlay for the local proof only: PostgreSQL and a volume for files. |
| `compose.e2e.yml` | Overlay for the end-to-end suite only: closes the API's and the worker's way out. |
| `.env.example` | Every name the services read, by service, without values. |
| `scripts/deploy.sh`, `rollback.sh`, `smoke.sh` | See below. `local.sh` runs the local proof; `lib.sh` is shared. |

PostgreSQL and the file store are **not** in `compose.yml`. They live on the database hosts (ADR-014,
ADR-015, ADR-020) and are reached through `QD_DATABASE_URL`, `QD_ADMIN_DATABASE_URL`,
`QD_WORKER_DATABASE_URL`, `QD_MIGRATION_URL` and `QD_S3_*`.

## Prerequisites

- A Linux host with Docker Engine and the Compose plugin (written against Engine 29 and Compose 5),
  `git`, `bash`, `curl`. Ports 80 and 443 reachable from the internet; nothing else is published.
- A clone of this repository on the host. The scripts build images from a **commit** (`git archive`),
  never from the working tree, and use the compose file and nginx configuration of that commit's images
  but the scripts and `compose.yml` of the checkout: keep the checkout at the release being deployed.
- A PostgreSQL 16 reachable from the host, with the database created, and an S3-compatible file store
  (ADR-020). Neither is set up by anything here.
- A certificate and its key for the public host name.

## One-time setup

1. **Env file.** `sudo install -d -m 700 /etc/qarz && sudo install -m 600 deploy/production/.env.example
   /etc/qarz/production.env`, then fill it in. The root `.env.example` says what each name means and how
   to generate each secret. Give the file to the user who deploys; it is never committed and never
   printed by the scripts. A value that contains `$` goes in single quotes.
2. **Certificate.** The proxy reads `fullchain.pem` and `privkey.pem` from `DEPLOY_TLS_DIR` (default
   `/etc/qarz/tls`), mounted read-only, as user 101. With certbot and the HTTP-01 challenge, which the
   proxy serves from `DEPLOY_ACME_DIR` (default `/var/lib/qarz/acme`) on port 80:

   ```sh
   sudo install -d /var/lib/qarz/acme /etc/qarz/tls
   # first issue: the proxy cannot start without a certificate, so use certbot's own listener once
   sudo certbot certonly --standalone -d qarz.example.uz
   # every renewal after that goes through the running proxy
   sudo certbot renew --webroot -w /var/lib/qarz/acme --deploy-hook /etc/qarz/install-cert.sh
   ```

   where `/etc/qarz/install-cert.sh` copies the two files out of `/etc/letsencrypt/live/<name>/` into
   `/etc/qarz/tls`, makes them readable by user 101 only (`chown 101 … && chmod 400 …`), and reloads the
   proxy (`docker compose -p qarz exec proxy nginx -s reload`). This hook is described, not written, and
   no certificate has been requested from any authority.
3. **Database roles.** Each part of the application connects as a role of its own, and the migrations
   create all three without a login, so the very first `deploy.sh` runs the migrations and then stops:
   the worker cannot connect. As the owner, give each role a password of its own, then run `deploy.sh`
   again:

   | Role | Who connects as it | Setting | Statement |
   |---|---|---|---|
   | `qd_app` | the API: shop members, customers, sign-in, the bot's chat | `QD_DATABASE_URL` | `ALTER ROLE qd_app LOGIN PASSWORD '…'` |
   | `qd_admin` | the API's administrators' side, and `rotate_secrets` | `QD_ADMIN_DATABASE_URL` | `ALTER ROLE qd_admin LOGIN PASSWORD '…'` |
   | `qd_worker` | the worker, and `measure_export` | `QD_WORKER_DATABASE_URL` | `ALTER ROLE qd_worker LOGIN PASSWORD '…'` |

   Three different passwords: the roles exist so that one part cannot act as another (security review,
   finding 11), and a shared password would undo that. `compose.yml` hands the worker only the worker's
   connection and the API only the other two. The API refuses to start when `QD_ADMIN_TG_IDS` names
   somebody and `QD_ADMIN_DATABASE_URL` is empty; the worker refuses to start without
   `QD_WORKER_DATABASE_URL`.
   The two commands run in the service that holds their connection:
   `docker compose run --rm api python -m qarz.interface.rotate_secrets` and
   `docker compose run --rm worker python -m qarz.interface.measure_export`.

   **A database that was created before migration `0031`.** That migration creates `qd_admin` and
   `qd_worker` and takes from `qd_app` what only they need. The release that brings it needs the two
   new settings in the env file and the two new roles able to log in. Create them, as the owner, before
   deploying: `CREATE ROLE qd_admin LOGIN PASSWORD '…'; CREATE ROLE qd_worker LOGIN PASSWORD '…';`
   (plain roles, with no other attribute). The migration leaves a role that exists as it finds it and
   grants it its rights, so one `deploy.sh` is then enough. Between the migration and the restart of
   the worker and the API, a few seconds, the release still running has lost its administrators' side
   and its worker: deploy it at a quiet hour. Nothing of this was done on a server; it was proven in
   the tests and in the local stack only.
4. **State directory.** `sudo install -d -o <deploying user> /var/lib/qarz/deploy`. The scripts keep the
   current and the previous release there.

## Deploy

```sh
deploy/production/scripts/deploy.sh <git-ref>
deploy/production/scripts/smoke.sh https://qarz.example.uz
```

`deploy.sh` resolves the reference to a commit, builds both images tagged with that commit (or reuses
them, or pulls them when `DEPLOY_PULL=1` and the image names point at a registry), runs
`alembic upgrade head` as the one-shot `migrate` service with the owner connection, restarts the worker,
then the API, then the proxy, waits for `/healthz` asked from the proxy's container, records the release
and prints what runs. It stops at the first step that fails. Replacing the API and the proxy containers
interrupts service for a few seconds; there is one API process by design (its rate-limit counters and
metrics live in that process), so there is no rolling restart.

`smoke.sh` checks from outside, changing no data: `/healthz`; HTTP redirects to HTTPS; the security
headers, each exactly once; the frame rules of the three pages; caching; `/metrics` unreachable by six
spellings; an unauthenticated API call answers 401 JSON with the proxy's `X-Request-Id`; the three entries
and their scripts; the body limits on ordinary and upload routes; the sign-in limit answers 429. It uses
up the sign-in limit of the address it runs from for about twenty seconds.

## Roll back

```sh
deploy/production/scripts/rollback.sh <previous-ref>     # the last one: cat /var/lib/qarz/deploy/previous
```

Starts the previous images, worker then API then proxy. It never runs a migration and never
`alembic downgrade`: the migrations have no tested downgrade, and each is compatible with the release
before it, so the previous code runs on the newer schema. It refuses when the images of that commit are
not on the host. Damaged data is a point-in-time restore (runbook 3), not a roll back.

One migration is an exception to "the previous code runs on the newer schema": `0031`, which splits the
database roles. A release from before it connects every part as `qd_app`, which after `0031` may no
longer do what the administrators' side and the worker do. To roll back across it, first run, as the
owner, `GRANT qd_admin, qd_worker TO qd_app;`, which gives the old role back everything the old release
used. When the newer release is deployed again, take it back: `REVOKE qd_admin, qd_worker FROM qd_app;`.
While the grant is in place the three roles are one again.

## Logs

Every service writes JSON lines to its standard streams; Docker keeps them on the host with the
`json-file` driver, at most ten files of 20 MB a service (`docker compose -p qarz logs <service>`).

- **proxy:** one line a request: time, `request_id`, client address, scheme, method, path, status, sizes,
  duration, upstream time, whether a limit applied. **No query string**, body, cookie, referrer or user
  agent. nginx's error log is set to `crit`, because its lines quote the request line with the query
  string and cannot be reformatted; what it would have said is in the access log's status.
- **api, worker:** the application's own log (`deploy/monitoring/README.md`). The proxy makes the request
  identifier and the API keeps it, so the two logs join on `request_id`.

The operations document asks for 30 days. Size-based rotation does not promise days, nothing ships the
logs anywhere, and the client address in the proxy's log is personal data with no retention job. All
three are open.

## Decisions taken where the documents are silent

| Subject | Decision | Why |
|---|---|---|
| Proxy | nginx | The founder's choice (DEC-058). The architecture document named Caddy and was amended to say nginx on 2026-10-08. |
| Paths | `/app/`, `/panel/`, `/admin/`, `/assets/` static; `/api/`, `/tg/webhook`, `/pay/`, `/files/`, `/healthz` to the API; `/` redirects to `/panel/`; everything else 404 (the API's `/docs` and `/openapi.json` included) | The build's own layout and the specification's paths. |
| `/metrics` | 404 from outside, in every spelling | Read it inside the compose network: `api:8000/metrics` with the bearer token. No monitoring system is attached yet. |
| Body limits | 1 MiB default; 5 MiB + 16 KiB on the two receipt routes; 5 MiB on the import route; 16 KiB on `/pay/` | The application's own numbers and route patterns, byte for byte. nginx cannot tie a size to a method, so a non-POST request on an upload route passes the proxy and is held to 1 MiB by the application. |
| Limits by address | general 20 requests/s, burst 40; the pages and their files (`/app/`, `/panel/`, `/admin/`, `/assets/`) 100/s, burst 300; sign-in (`/api/v1/auth/`, `/api/admin/v1/auth/`) 30/minute, burst 10; `/pay/` 5/s, burst 10; `/tg/webhook` 30/s, burst 60; 100 connections | Mobile operators put many people behind one address, so the general limit is generous and the application's per-user and per-shop limits do the fine work. |
| `/tg/webhook` | Its limit is **wider** than the general one, not stricter | Telegram sends every shop's updates from a few addresses; a narrow limit would drop updates. It is narrowed otherwise: POST only, and `snippets/allow-telegram.conf.example` restricts it to Telegram's published networks once someone has watched that work. |
| TLS | 1.2 and 1.3, the Mozilla "intermediate" suites, no session tickets | The specification says "TLS 1.2 or later". |
| HSTS | `max-age=31536000`, no `includeSubDomains`, no `preload` | The other host names under the founder's domain are not known. |
| CSP, Mini App | scripts from itself and `https://telegram.org`; styles, fonts, connections from itself; images from itself and `blob:`; framed only by `web.telegram.org`, `webk.telegram.org`, `webz.telegram.org` | What `frontend/app/index.html` loads. Inside Telegram's web client the bridge script adds a `<style>` element for scroll bars, which `style-src 'self'` refuses; nothing else is affected. |
| CSP, panel and admin | the same, plus `frame-src https://oauth.telegram.org`, `frame-ancestors 'none'`, `X-Frame-Options: DENY`. **No `'unsafe-eval'`** | The pages use the Login widget's redirect mode (`data-auth-url`): Telegram's script sends the browser back to the page with the signed fields in the query string, and the page takes them out of the address before anything else (`frontend/src/panel/loginReturn.ts`). The widget's callback mode (`data-onauth`) is what needed `eval()`. `smoke.sh` fails if any policy allows it. See "Signing in without eval" below. |
| Referrer | `strict-origin-when-cross-origin` | Another site learns the origin at most; `no-referrer` was not chosen because whether Telegram's sign-in frame needs the origin was not tested. |
| Permissions | everything off except `clipboard-write` for the page itself | The pages copy a code and a card number; they use no camera, location or sensors. |
| Caching | `no-store` for HTML, the API and files; one year `immutable` for `/assets/` | Asset names carry a content hash. |
| Compression | gzip for static text files only; none for the API; no brotli | Brotli is not in the stock image. API answers are small, and compressing answers that carry secrets is avoidable. |
| Request identifier | Always made by the proxy (32 hexadecimal characters); one sent by a caller is ignored | So a caller cannot choose what appears in the logs. |
| Forwarded headers | `X-Forwarded-For` is the caller's address only; the API trusts forwarded headers from any peer | The proxy is the first hop, and the API is reachable only on a network with no other member. Behind the Cloudflare Tunnel the proxy is the second hop, and takes the visitor's address and scheme from Cloudflare's headers only when the peer is the tunnel's container ([SINGLE-HOST.md](SINGLE-HOST.md), "The proxy behind the tunnel"). |
| Timeouts | 3 s to connect to the API, 20 s between reads or writes, 15 s for a client's body, 10 s for its headers | The API cancels a statement at 5 s; a request may be several statements or an upload. |
| Host names | `server_name _`: any host name is answered | The public name is not known yet. Restrict it when it is. |
| Containers | read-only root, all capabilities dropped, `no-new-privileges`, tmpfs `/tmp`; limits: API 1 CPU / 512 MB, worker 1 CPU / 768 MB, proxy 1 CPU / 192 MB, migration 1 CPU / 256 MB | Starting points, not measured under load. |
| Worker health | No health check; the restart policy restarts a dead worker | It listens on nothing. A worker that runs but does no work shows in the metrics. The worker watches the rest of the service and tells the operators' Telegram chat (`QD_ALERT_CHAT_IDS`; `deploy/monitoring/README.md`, "The worker's watch"); nothing watches the worker. |
| Images | Tagged with the full commit hash; built on the host because there is no registry | `DEPLOY_PULL=1` and the two image names switch to pulling. Base images are pinned by tag, not by digest. |
| Local ports | 18480 and 18443 on 127.0.0.1, project `qd-deploy-proof` | 80, 443, 8000 and 54329 are taken or reserved on the developer machine. |

## Signing in without eval

The panel and the administrators' entry give Telegram's Login widget `data-auth-url` (the page's own
address) instead of a callback. After the person confirms in Telegram, Telegram's script on our page sets
`location.href` to that address with `id`, `first_name`, `auth_date`, `hash` and the other signed fields
as a query string. What happens to them:

- **Address bar and history.** The page's script, before it draws or requests anything, reads the fields
  and replaces the address in the same history entry with one that has no query string. No step back or
  forward leads to an address with the fields. The browser's own list of visited addresses may still
  hold the address it loaded; the server accepts each set of signed fields once, so that address opens
  nothing afterwards.
- **Logs.** The proxy's access log has the path without the query string and no referrer (`local.sh
  smoke` checks the first). The application's request log has the route template only. The page's own
  scripts and style sheet are requested while the address still carries the fields, so those few
  requests, to this proxy only, carry them in `Referer`; nothing logs that header.
- **Other sites.** `Referrer-Policy: strict-origin-when-cross-origin` gives another site the origin at
  most, and the page requests nothing from another site before the address is replaced.
- **Somebody else's fields in a link.** A link to the page with another person's valid fields would sign
  the reader in to that person's account. The page therefore takes fields only when the browser says the
  navigation came from this same site (`document.referrer`), which is true when Telegram's script on our
  page navigates and false for a link in a message or on another site. Nothing is kept in browser
  storage for this. The cost: a browser set to send no referrer at all cannot sign in to the panel.
- **CSRF token.** Unchanged: answered by the sign-in call, kept in memory only.

## The local proof

```sh
git commit …                                   # the images are built from the commit
deploy/production/scripts/local.sh up          # certificate, env file, database, deploy.sh HEAD
deploy/production/scripts/local.sh smoke       # smoke.sh, then checks that need the host
deploy/production/scripts/local.sh down        # containers, networks, volumes, images, generated files
```

Everything generated is under `deploy/production/.local/`, which git ignores. CI builds both images, checks
the compose files and runs this same proof on every pull request, and runs the end-to-end suite (below)
against the same images.

## The end-to-end suite

`e2e/` drives the built front end in Chromium (Playwright) against this stack: the real API, worker and
PostgreSQL behind the proxy, so the policy, the limits and the headers above are in force. It is a
package of its own, so that `frontend/` installs and bundles nothing of it.

```sh
git commit …                       # the images are built from the commit, as for the local proof
e2e/stack.sh up                    # project qd-e2e on https://127.0.0.1:28443 (http: 28480)
cd e2e && npm ci && npx playwright install chromium
npm test                           # about a minute and a half; npx playwright test tests/04 for one file
e2e/stack.sh down                  # containers, networks, volumes, images, generated files
```

- **Nothing leaves the machine.** `compose.e2e.yml` makes the network of the API and the worker
  internal, so the worker's calls to Telegram fail at once; what it would have sent is read from the
  `outbox_message` table. The browser's requests for Telegram's two scripts are answered by stand-ins
  (`e2e/support/fixtures.ts`), and a request to anywhere else fails the test.
- **Signing in** is done the way Telegram would do it: launch data and Login-widget fields signed with
  the stack's made-up bot token (`e2e/support/telegram.ts`), and chat updates posted to `/tg/webhook`
  with the stack's webhook secret. The application has no test entrance.
- **The database** is read with `psql` as its owner, in read-only transactions.
- **Every journey** fails on a Content-Security-Policy violation reported by the browser and on an API
  answer without `X-Request-Id`.
- An administrator's second factor is enrolled once, so the stack's allow-list holds eight made-up
  identifiers and each run uses the next one; after eight runs, `down` and `up` again.
- `down` removes every `qarz-daftari/backend` and `qarz-daftari/proxy` image on the machine, the local
  proof's included. Generated files are in `deploy/production/.local-e2e/`, which git ignores.

## Not covered here

This list is about `compose.yml` on its own, the two-server design. On the single host the database,
its backups, the file store and the way in are covered by `compose.single-host.yml`
([SINGLE-HOST.md](SINGLE-HOST.md)); what stays uncovered there is in that file's "What is watched" and
"Not proven".

- The database hosts: PostgreSQL primary and standby, replication, failover (ADR-014, ADR-015;
  `deploy/rehearsal/` is a rehearsal on one machine).
- Backups and restore: `deploy/backup/` (configuration and scripts for the database hosts; proven in
  containers only).
- The file store (ADR-020) and its replication.
- The monitoring system, alert delivery, the external check of `/healthz`, log shipping and retention.
- The host itself: firewall, operating-system updates, users, file modes of the secrets, disk.
- DNS, and a real certificate with its renewal.
- Staging, and the automatic deployment of `main` to it that the operations document describes.
- An image registry and the standby's images.
- What only the founder can do: registering the Telegram webhook (`setWebhook` with the public address and
  `QD_WEBHOOK_SECRET`), setting the bot's domain for Telegram Login in BotFather (`/setdomain`) and the
  Mini App address, renting the servers and the domain.

## Not proven

- Anything on a real server, under real traffic or load.
- A certificate from a real authority, and its renewal through the proxy.
- The Mini App inside Telegram, on any client: the frame rules for Telegram's web client are written from
  Telegram's script, not observed.
- Signing in with the real Telegram Login widget: it needs a real bot whose domain is set in BotFather.
  What was proven is the page's half: the end-to-end suite (`e2e/`) signs in to `/panel/` and `/admin/`
  under the policy above with a stand-in for Telegram's script that does what the script's redirect mode
  does, and no policy violation is reported. What was only read, not run: `telegram-widget.js?22` reaches
  `eval()` solely through `data-onauth` and `data-onunauth`, and in redirect mode navigates with
  `location.href` from our page. Not proven: that Telegram's own script draws its button and completes a
  sign-in under this policy, and that Telegram accepts the page's address as `data-auth-url` for the
  bot's domain.
- That the proxy sees callers' real addresses. On Docker Desktop every caller appears as the bridge's
  gateway; on a Linux host with published ports the real address is expected, and the limits by address
  mean nothing until that is checked in the access log. (Behind the tunnel the address comes from
  Cloudflare's header instead; that path is proven with a stand-in for the tunnel, not with Cloudflare:
  [SINGLE-HOST.md](SINGLE-HOST.md).)
- The S3 file store path: the local proof uses a directory.
- A release with a migration deployed and rolled back against a database with data.
- The resource limits and the rate numbers: none was tuned against a measurement.
