# Security review (story S19.2)

Prepared 2026-10-07 by an independent reviewer agent that did not write the code under review.
Requirements: REQ-N11, REQ-N12, NFR-013. Launch criterion 7 (`OUTPUT.md`, "Production launch criteria").

This document reports what was read, what was run, and what was found. It does not state that the service is safe to launch. Launch criterion 7 asks for a manual attempt to break tenant isolation and, if affordable, an outside reviewer; a person still has to do both. Nothing here was tested against a deployed system, because none exists.

## What has been done about the findings since

The review below is kept as it was written. This section is added by the build and records what changed afterwards; each fix turned the review's test for it from an expected failure into an ordinary test.

| Finding | State | How |
|---|---|---|
| 1 | Fixed | The membership lookup names the shop itself, as well as row-level security. No start-up check of the database role was added |
| 2 | Fixed | A trigger holds the waiting period: the application role cannot ask for deletion with a due time less than 30 days ahead by the database's clock, bring a due time forward, mark a shop erased, or bring an erased shop back |
| 3 | Fixed | Every function that runs with its owner's rights has the temporary schema last in its search path; a test fails for any new one that does not |
| 4 | Fixed in the application | Bodies over one mebibyte are refused with 413 before anything is read or parsed. The proxy's own limit still does not exist |
| 5 | Fixed | The nine operations are refused in a suspended shop. Asking for and cancelling deletion were left as they are, as the review suggests, for the founder to decide |
| 6 | Fixed | The signature is compared as bytes |
| 7 | Fixed | Statement parameters are kept out of database errors |
| 8 | Fixed | Rate limits per user and per shop (pull request 39) |
| P39-1 | Fixed | Answers a stranger can get (404, 422) do not count against a shop |
| 10 | Fixed | Chat request keys contain colons, which an API key may not. Opening a shop from the chat keeps an API-shaped key |
| 9 | Fixed in part | Signed sign-in data is accepted once: a hash of its signature is recorded, in the transaction that creates the session, until the data would be refused for its age; a second use is answered 401 like a wrong signature and the first session stays valid. The worker purges, once an hour, used sign-in records past their expiry, sessions and administrator sessions that expired or were revoked, and administrator request keys older than 30 days. A signed-in person can end all of their own sessions, Mini App and web, on every device, the current one included (`POST /api/v1/auth/sign-out-everywhere`; a button with a confirmation in the Mini App and in the shop panel); the next request of each of those sessions is refused as signed out. The administrators' panel needed nothing new: an administrator holds one admin session at a time, and closing it ends every admin session of that administrator. Not done: a shorter age for the web login, and ending the previous web session at a new web sign-in |
| 11 | Fixed in part | Migration 0027 leaves the application role, on each platform table, only what the application does with it; a test lists the rights and fails for a platform table that is not listed. `platform_setting` is read-only for the role and is changed through `admin_set_platform_setting`, which finds an active, confirmed administrator with an open session itself and writes the audit row with the setting. The Telegram identifier of a person, a session's owner and lifetime, an administrator account's status, and a queued message's recipient and text can no longer be rewritten; none of these tables can be deleted from. The role also lost its rights on `alembic_version`. Migration 0031 (DEC-068) gives each part a role of its own: `qd_app` for the ordinary application, `qd_admin` for the administrators' side, `qd_worker` for the worker, each granted only what its code runs; a test lists every table right and every `SECURITY DEFINER` function with the roles that may execute it, and fails for one that is not listed. `qd_app` no longer reads `admin_account`, `admin_session`, `admin_audit` or `admin_request_key`, cannot call any administrator's or worker's function (so it cannot erase a shop, purge sessions or claim a job), and of `outbox_message` can only queue: it reads five columns of a queued row and not the recipient or the text. Still so, by decision: `qd_app` reads the card number, because every owner is shown it to pay (DEC-061), and inserts sessions, because it is the part that signs people in. Not done: the administrators' side is served by the same process as the ordinary API, so that process holds both connections and a full compromise of it reaches both; only the worker is a process of its own |
| 12 | Fixed | The first half was fixed with the request identifier (`interface/observability.py`). The application now sets `Cache-Control: no-store` and `X-Content-Type-Options: nosniff` on every answer under `/api/` itself, so it holds without the proxy; a route that names its own caching rule keeps it |
| 13 | Fixed in part | A person gets one trial; later shops start in limited mode, decided by `claim_owned_shop` in the transaction that creates the shop. The limit of five shops a person, with its refusal `SHOP_LIMIT_REACHED`, was removed by the founder on 2026-10-08 (DEC-065, migration 0029): a person may own any number of shops, so what bounds the creation of shops is again only the general rate limits. A limit on new shops per day is closed by the same decision and will not be added: the founder removed the limit on shops and kept the rate limits (DEC-065). Not done: a cap on waiting entries per shop |
| P39-2, the notes | Open | Not addressed. A proxy configuration now exists under `deploy/production/nginx/`; the notes were not checked again against it |
| P36-1 to P36-3 | Open | To be settled in pull request 36 before it is merged |

## Scope and method

Reviewed: branch `main` at commit `1bf48b9` (after pull request 38), plus the diffs of the open pull requests 36 (payment notices and the file store), 39 (rate limits) and 40 (web panel sign-in, sign-in part only).

Method:

1. Read the promises: PRD (REQ-N11, REQ-N12), architecture ("Security boundaries"), technical specification ("Authentication / authorization", "Security", "Error handling", "Cross-tenant functions"), operations (launch criterion 7), `PROGRESS.md`.
2. Read the code that keeps them, without relying on comments or test names.
3. For every suspicion, wrote a probe against the real application and a real PostgreSQL 16 database, connected as the restricted role `qd_app` unless stated otherwise. A suspicion that the probe disproved is listed under "Checked and found sound". One that it confirmed is a finding with a committed test.
4. Severity is the reviewer's judgement on this scale:

| Severity | Meaning |
|---|---|
| Critical | Any outsider reads or changes another shop's data, or signs in as someone else, with no precondition |
| High | Any signed-in user can do so |
| Medium | A promised control is missing or does not hold, and it is reachable directly or after one realistic mistake (a wrong setting, a flaw in one query) |
| Low | Hardening; wrong behaviour without a direct loss of confidentiality or integrity |
| Note | A design choice or gap the founder should know about; not a defect in the code |

No Critical or High finding was made on `main`. That is a statement about what this review looked at, listed below, and not about what it did not.

## Added after the review: the SMS sender (Eskiz)

Not part of the review above and not seen by its reviewer; recorded here by the build so that the next
review knows it exists. `infrastructure/eskiz_sms.py` and `infrastructure/sms_sender.py` send reminders
to Eskiz (`notify.eskiz.uz`) from the worker, behind the platform switch `sms_on`, which is off.

- **New secrets:** `QD_ESKIZ_EMAIL`, `QD_ESKIZ_PASSWORD` (and the sender name `QD_ESKIZ_SENDER`), read
  from the worker's environment only; the API's service is not handed them
  (`backend/tests/test_deploy_files.py`). The token Eskiz returns lives in the worker's memory, is never
  written to the database and never logged.
- **New outbound flow of personal data:** a customer's phone number, their name as the shop wrote it, the
  shop's name and the amount owed go to Eskiz and on to a mobile operator. It is the first such flow
  besides Telegram, and belongs with the open legal questions ("SMS without consent", specification).
- **Logs and errors:** a send is logged by a fixed word, the HTTP status and nothing else; exceptions
  carry a fixed word. A test sends failing messages whose provider answers and network errors repeat the
  number, the text, the token and the password, and fails if any of them reaches a log record or an
  exception (`backend/tests/worker/test_eskiz_sms.py`).
- **Transport:** HTTPS to a fixed host, certificate checked by the standard library's defaults, no
  redirects followed, no proxy from the environment, 10 seconds a socket operation and 20 a call, answers
  read up to 64 KiB. The multipart body is built by the sender from fixed field names.
- **No new endpoint:** delivery reports (`callback_url`) are not taken, so nothing new listens.
- **Database:** no new right. Migration 0032 adds one index for the health figures.
- **Not reviewed by anyone but its author, and never run against Eskiz.**

## What was read

| Area | Files |
|---|---|
| Sign-in and sessions | `backend/src/qarz/application/auth.py`, `interface/auth_api.py`, `domain/telegram_auth.py`, `interface/http.py`, `interface/asgi.py`, `infrastructure/settings.py` |
| Authorization | `application/operations.py`, `domain/access.py`, `application/shops.py`, `customers.py`, `ledger_service.py`, `links.py`, `staff.py`, `ownership.py`, `disputes.py`, `customer_account.py`, `date_requests.py`, `account.py` (active shop), `idempotency.py`; the first statements of every tenant transaction in every service (by search); `interface/customers_api.py`, `shops_api.py`, and the request models of the other `*_api.py` files |
| Database | all 15 files in `backend/migrations/sql/`; in `infrastructure/db.py` the platform session, the `Database` class, the tenant session up to the customer queries, and every `SECURITY DEFINER` call |
| Webhook and chat | `interface/telegram_webhook.py`, `application/telegram_updates.py`, `application/chat.py` (whole file as of `cba9213`; the 129 lines added by pull request 38 were not read) |
| Outbound and logs | `infrastructure/telegram_sender.py`, `interface/worker.py`, every `logging` call |
| Front end | `frontend/src/app/main.tsx`, `panel/main.tsx`, `shared/session.ts`, the transport in `shared/api.ts`, `app/index.html`, `panel/index.html`, `vite.config.ts`; a search of `frontend/src` for storage, HTML injection, cookies, external addresses and links |
| Configuration | `.env.example`, `.gitignore`, `docker-compose.dev.yml`, `deploy/rehearsal/` (compose file, init script, file store configuration), `.github/workflows/ci.yml`, `backend/pyproject.toml` |
| Pull request 36 | `domain/files.py`, `application/files.py`, `infrastructure/file_store.py`, `infrastructure/telegram_files.py`, `application/payment_notices.py`, `interface/payment_notices_api.py`, `migrations/sql/0012_payment_notices.sql` |
| Pull request 39 | `interface/rate_limit.py` and its wiring in `interface/http.py` |
| Pull request 40 | `frontend/src/panel/TelegramLogin.tsx`, `panel/signIn.ts`, `panel/main.tsx` |

## What was run

| What | Result |
|---|---|
| Backend tests on the review branch, one directory at a time: `tests/api`, `tests/db`, `tests/worker`, the remaining unit tests | All pass; the 17 tests of this review that demonstrate findings are reported as expected failures |
| 165 probe requests: each of the 55 shop operations of `cba9213` as owner, manager and seller of a suspended shop (the four operations pull request 38 added were read, not probed) | Finding 5 |
| Probe: the application connected as a role that bypasses row-level security | Finding 1 |
| Probe: foreign identifiers in request bodies, against random identifiers | Sound; kept as a test |
| Probes as `qd_app` with its own SQL: shop erasure, temporary tables, platform tables | Findings 2, 3, 11 |
| Probe: text of a database error | Finding 7 |
| Probe on pull request 39's head (`6fc232d`) in a throwaway checkout: requests that fail validation, sent by a stranger | Finding P39-1 |
| `npm audit --omit=dev` and `npm audit` in `frontend/` | 0 vulnerabilities |
| `pip-audit --disable-pip --no-deps -r requirements.lock` | No known vulnerabilities. The form CI uses (`--require-hashes`) cannot resolve on Windows, because the lock is for Linux and lacks `tzdata`; CI runs it on Linux |
| Search of every commit on every branch (91 when it was run) for Telegram bot tokens, private keys, cloud and GitHub keys, and for any `.env` file ever added | Nothing found. Only `.env.example` was ever committed, and it holds no secret. Test files contain fixed test-only strings |
| `ruff format --check`, `ruff check` on the added tests | Pass |

The `.env` file of the main checkout was not opened.

## Findings

Tests are in `backend/tests/api/test_security_review.py` (API) and `backend/tests/db/test_security_review_db.py` (DB). Each test that demonstrates a finding is marked `xfail(strict=True, reason="security review finding N")`: it fails today, and once the defect is fixed it passes and the strict mark fails the suite until the mark is removed.

### Medium

| No. | Where | What an attacker can do | Verified | Recommended fix |
|---|---|---|---|---|
| 1 | `backend/src/qarz/infrastructure/db.py:269` (`active_membership`), and every other tenant query; `application/shops.py:25` (`require_member`) | The membership lookup is `WHERE user_id = :user_id AND status = 'active'` with no shop condition. It is correct only because row-level security hides other shops' rows. So the application check and the database policy are one control, not the two that REQ-N12 promises ("below the application code as well as in it"). If the application is ever started with a role that bypasses row-level security (a superuser, or the migration owner in `QD_MIGRATION_URL` pasted into `QD_DATABASE_URL`, for instance during a failover), every member of any shop passes as a member of every shop, including shops that do not exist, and list queries return all shops' rows. Nothing refuses to start in that state. | API test `test_a_stranger_is_refused_even_when_the_database_role_bypasses_row_level_security`: owner of shop B receives shop A's customers with status 200 | (a) At start, query `pg_roles` for the connected role and refuse to serve if `rolsuper` or `rolbypassrls` is true; add the same check to the deep health check. (b) Add `AND shop_id = :shop_id` to `active_membership` at least, so that the role check stands on its own. |
| 2 | `backend/migrations/sql/0017_shop_erasure.sql:35`; `infrastructure/db.py:1591` (`set_deletion`); grant in `0001_initial.sql:377` | The ledger is insert-only for `qd_app` (REQ-N07), with erasure as the one exception. `erase_shop` decides from `shop.status` and `shop.deletion_due`, and `qd_app` may update both. Two statements as `qd_app` (set the due time in the past, call the function) delete every entry, promise and activity row of any shop at once. The migration's own comment, "the application cannot shorten the waiting period either", is therefore not true. Reaching this needs a flaw that lets an attacker run or influence SQL as the application role; given that, the 30-day wait (BR-25) and ledger immutability hold only in application code. | DB test `test_the_application_role_cannot_erase_a_ledger_before_the_waiting_period` | Move the request into the database: a `SECURITY DEFINER` function `request_shop_deletion()` that sets `deletion_due = now() + interval '30 days'` itself, and a cancel function; revoke `UPDATE (status, deletion_due)` on `shop` from `qd_app` (column-level grant for the other columns). |
| 3 | Every `SET search_path = public` clause in `backend/migrations/sql/0002` to `0017` (15 clauses, 14 functions) | With `search_path = public`, PostgreSQL still searches the session's temporary schema first for tables, and `qd_app` may create temporary tables (the default `TEMPORARY` privilege of `PUBLIC`). A temporary table named like a real one changes what a `SECURITY DEFINER` function reads while its writes still go to the real tables. Shown: a made-up `invitation` row makes `accept_staff_invitation` insert a real manager membership in a chosen shop. The same trick applied to `shop` makes `erase_shop` delete a shop's ledger. Same precondition as finding 2. | DB test `test_a_temporary_table_cannot_stand_in_for_a_table_a_definer_function_reads` | A new migration that sets `search_path = pg_catalog, public, pg_temp` on every such function (`ALTER FUNCTION ... SET search_path`), and `REVOKE TEMPORARY ON DATABASE ... FROM PUBLIC` at deployment. Extend `test_every_definer_function_pins_its_search_path_and_is_closed_to_public` to require `pg_temp` last. |
| 4 | `backend/src/qarz/interface/http.py:76` (no size limit on the application); no proxy configuration exists under `deploy/` | Anyone, without signing in, can post a body of any size to any API route. FastAPI reads and parses the JSON body before the session is looked at, so memory and CPU are spent for callers who are then answered 401. The architecture assigns "request size and rate limits" to the proxy, and the repository contains no proxy configuration. | API test `test_an_oversized_body_from_nobody_is_refused_before_it_is_read`: 3 MB accepted, answer 401 instead of 413 | Refuse by `Content-Length` and by counted bytes in a small ASGI middleware (64 KB is ample for every JSON route; the upload route of pull request 36 already has its own limit), and also set the limit in the proxy when it is written. |
| 8 | `backend/src/qarz/interface/http.py`; `PROGRESS.md` line 102 | `main` has no rate limit at all: sign-in, invitation acceptance, every read and write. The specification promises 120 requests a minute per session, 30 writes a minute per user, and tighter limits on authentication and upload. Tokens are 256-bit, so guessing is not the risk; cost and noisy-tenant behaviour are. Pull request 39 adds per-user and per-shop limits; see P39-1 and P39-2 before merging it. | Read only; stated as not implemented in `PROGRESS.md` | Merge a corrected pull request 39; limit unauthenticated routes by address in the proxy; add the per-user write limit the specification names. |
| P39-1 | Pull request 39, `interface/http.py` (middleware `count_for_the_shop`) and `interface/rate_limit.py` (`answered`) | The shop's bucket is charged for every answer that is not 404, on the assumption that only a member gets one. A stranger gets 422 for a request that fails validation (for example `GET /api/v1/shops/{id}/customers?limit=abc`), because validation runs before membership is checked. Each such request takes a token from that shop's bucket and marks the stranger as a member for ten minutes. A signed-in person who knows a shop's identifier (a removed employee does) can keep the bucket empty and lock the shop's staff out with 429. The pull request states "strangers cannot use up a shop's rate"; that does not hold. | Probe on `6fc232d` with a shop limit of 4: four 422 answers to a stranger, then 429 for the owner's second request and for a seller. Not committed, because the code is not on `main` | Charge the shop only when `require_member` succeeded: let it set a flag on the request (or return through a context variable) and count on that, not on the status code. Add the stranger-with-invalid-query case to `tests/api/test_rate_limits.py`. |

### Low

| No. | Where | What goes wrong | Verified | Recommended fix |
|---|---|---|---|---|
| 5 | `application/shops.py` (`update`), `staff.py` (all writes), `ownership.py` (all writes): none calls `require_writable` | The specification: "in suspended mode only owner viewing and export remain" (BR-30). In a suspended shop the owner can still rename the shop, invite, change, remove staff, and start or cancel an ownership transfer; a manager can accept or decline one, so a suspended shop can change owner. Joining through `/start` with an invitation is not checked either (`accept_staff_invitation`). | API test `test_a_suspended_shop_accepts_no_change`, nine cases | Call `require_writable(session, today, new_credit=False)` in those operations; decide and record whether requesting or cancelling deletion stays allowed while suspended (left out of the test on purpose). |
| 6 | `backend/src/qarz/domain/telegram_auth.py:63` and `:85` | `hmac.compare_digest` raises `TypeError` when the supplied `hash` contains a non-ASCII character. Nobody is signed in, but an unauthenticated caller gets an unhandled 500 and writes a traceback to the log with each request. | API test `test_a_signature_with_a_non_ascii_character_is_refused_not_crashed_on`, both sign-in routes | Refuse a `hash` that is not 64 hexadecimal characters before comparing, or compare bytes (`given.encode()`). |
| 7 | `infrastructure/db.py:2055` (`create_async_engine` without `hide_parameters=True`); log calls at `interface/telegram_webhook.py:42`, `interface/worker.py:52` and `:57`; the server's own logging of unhandled errors | SQLAlchemy puts a failed statement's parameters into the exception text. Every place that logs an exception therefore logs names, phones, message payloads and token hashes when a write fails. The specification: logs hold "identifiers only; never message text, names, phones, card numbers, tokens". The comment in `worker.py:56` shows the authors expected otherwise. | DB test `test_a_database_error_does_not_carry_personal_data` | `create_async_engine(..., hide_parameters=True)`. Also configure the server's access log without query strings: customer search sends the typed name as `?q=`. |
| 9 | `application/auth.py:23` to `:25`; `0003_user_session.sql` | Signed Telegram data is accepted any number of times for one hour, and each use issues a new session (12 hours, or 14 days for the web). Whoever captures one launch string or login payload inside that hour holds the account for up to 14 days. This matches the letter of the specification (age limit only). Expired and revoked sessions are never deleted; there is no "sign out everywhere"; a new web sign-in does not end the previous web session. | Read only | Remember the signature of used login data until it expires and refuse a second use; shorten the age accepted for the web login to a few minutes; add a retention job for sessions and a way to revoke all of a user's sessions. |
| 10 | `application/chat.py:624`, `:699`, `:851` (`key=incoming.key`, the string `tg-update-<update_id>`); `application/idempotency.py:13` | Chat writes and API writes share one key space per shop. A staff member who sends an API write with the key `tg-update-N` first makes the chat message with update number N fail for a colleague in the same shop. Update numbers are not shown to staff, so this is hard to aim; it is a collision that should not be possible. | API test `test_an_api_request_key_cannot_block_a_colleagues_chat_entry` | Give chat keys a form the API pattern refuses (for example a colon: `tg:update:N`), or store the source with the key. |
| 11 | `0001_initial.sql:377` (`GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO qd_app`) | The application role can read and write every platform table: `platform_setting` (the card number owners are told to pay to, the price), `admin_account` (second-factor secrets, stored encrypted by the application), `app_user` (every Telegram identifier), `user_session` (it can insert a session for any user), and `outbox_message` (the text of every shop's queued messages, outside row-level security). One role serves the API, the worker and, later, the administration API. | Probe as `qd_app`: `UPDATE platform_setting` changed 1 row; reads of the other tables succeeded | Grant per table what is used. When administration lands, give it its own role, or put changes to `platform_setting` and reads of `admin_account` behind functions that check the administrator. Consider a separate role for the worker. |
| 12 | `interface/http.py` (no handler for unexpected exceptions; no response headers) | An unexpected error is answered with the framework's plain-text 500 and no reference identifier, where the specification promises the common error shape with one. No internals leak. API answers that carry personal data have no `Cache-Control: no-store` and no `X-Content-Type-Options`. | Read only; the 500 body was seen in the probe for finding 6 | A catch-all handler that logs an identifier and returns the common shape; a middleware that sets `Cache-Control: no-store` and `X-Content-Type-Options: nosniff` on `/api/`. |
| 13 | `application/shops.py:75` (`create`); `0008_customer_linking.sql` (`link_customer`, counter codes) | Any Telegram user can create any number of shops, each with a fresh trial, from the chat or the API. Anyone holding a shop's counter code can fill its waiting list with accounts (one per account), each under a profile name of their choosing. | Read only | A limit on shops per user and per day; a cap on waiting entries per shop; both through the rate limiter when it exists. |
| P36-1 | Pull request 36, `domain/files.py` (`sniff`, `check_receipt`) | A receipt is accepted on its first bytes alone. Images are not re-encoded, so metadata (for example the place a photo was taken) is kept, and a file that starts like a JPEG or a PDF but carries other content is stored and later handed to staff. The architecture names "virus and type checks"; no scan exists. Mitigated: the download is always an attachment with `nosniff`, so the browser does not render it in the application's origin. | Read only | Re-encode images on upload (drops metadata and trailing content); decide whether PDF receipts are needed at all; record the missing malware scan as a known gap. |
| P36-2 | Pull request 36, `application/payment_notices.py` (`receipt`) calling `files.read_in` inside `storage.tenant(...)` | The file is fetched from the store while a database transaction is open. With a pool of at most ten connections, a slow file store and a few staff opening receipts hold every connection, and the whole API waits. | Read only | Read the file's record inside the transaction, fetch the content after it has closed. |
| P36-3 | Pull request 36, `application/payment_notices.py` (`send`: `stage` before `_send_in`) | The receipt is uploaded to the store before the notice's own limits are checked. A linked customer whose notice will be refused (too many open, amount above balance) still makes the service upload and then delete up to 5 MB per request, as often as they like. | Read only | Check `may_send` once before staging (and again under the lock), and rate-limit the route. |
| P39-2 | Pull request 39, `interface/rate_limit.py` | Counters live in one process. The architecture describes "API (several worker processes)"; each process would allow the full rate. Unauthenticated routes, the webhook and the chat are not limited by this pull request (stated in it). | Read only | Record the single-process assumption as a deployment rule and check it at start, or keep counters in PostgreSQL. |

### Notes

| No. | Subject | Detail |
|---|---|---|
| 14 | No deployment to review | `deploy/` holds only the database rehearsal. There is no proxy configuration, no production compose file and no server setup. TLS and HSTS, the content security policy, frame rules for the Mini App, request size and address-based limits, non-root containers, the firewall and the file modes of secrets are all promised in the specification and exist nowhere yet, so none could be checked. The front end sets no policy itself (no `meta` policy in `app/index.html` or `panel/index.html`). |
| 15 | Counter code and waiting list | A counter code never expires until a manager replaces it, and any seller may attach a waiting person to any customer record. The only evidence of who the person is, is the name in their Telegram profile, which they choose. A person who obtains the code and waits under another customer's name gets that customer's balance, goods and notifications if a seller attaches them. This is the approved design (BR-16) and relies on the seller looking at the person in front of them. Consider showing the seller a short code that the customer must read out from their own chat. |
| 16 | Foreign keys do not carry the shop | Tenant tables refer to each other by single-column keys (`ledger_entry.customer_id`, `goods_line.catalog_item_id`, `customer_link.customer_id`, and so on). Referential checks ignore row-level security, so the database itself would accept a row of shop B that points at a row of shop A. Every service was found to look the target up inside the tenant first (see "Checked and found sound"), so no path to this was found. Composite keys `(shop_id, id)` would make it impossible rather than merely avoided; `0007_catalog_merge.sql` already does this for `catalog_item.merged_into` and says why. |
| 17 | Telegram's scripts | `app/index.html` loads `telegram-web-app.js`, and pull request 40 loads `telegram-widget.js`, from `telegram.org` without an integrity hash (Telegram does not publish versioned files, so none can be pinned). Either script runs with full access to the page, including the session token held in memory. This is accepted by the specification ("no third-party scripts except Telegram's") and should be recorded as a risk that is carried. |
| 18 | Continuous integration | Actions are pinned by tag (`actions/checkout@v5`), not by commit. The Python lock is hash-pinned and CI installs with `--require-hashes`; `npm audit` fails the build only at `high`. |
| 19 | Sessions and role changes | The specification says sessions are "rotated on privilege change". They are not, and need not be for staff: the role is read from the database on every request, so a demoted, suspended or removed member loses access at once (verified for suspended and removed members). |
| 20 | Receipt download in pull request 36 | The specification says files are "served through signed links valid 5 minutes" with "duplicate detection by hash". Pull request 36 serves the file through the API after a membership check and detects no duplicates. The direct download is at least as strict as a signed link; both differences should be recorded as decisions. A file staged in the store is orphaned if the process stops before the transaction commits; nothing sweeps such objects. `QD_S3_ENDPOINT` accepts plain `http`; that is acceptable only on the loopback or the private tunnel. |
| 21 | Uneven handling of a suspended shop for customers | A customer of a suspended shop can still open and withdraw a dispute (`application/disputes.py`, no subscription check), but cannot open a date request (`date_requests.py` refuses). Decide which is intended. |
| 22 | Pull request 40 | The CSRF token is kept in memory only, which is right; after a page reload the cookie is still valid but the token is gone, so the person signs in again and a second 14-day session is created while the first stays valid (see finding 9). |

## Checked and found sound

Each line says how it was checked.

**Sign-in and sessions**

- Session tokens and CSRF tokens are 256 random bits (`secrets.token_urlsafe(32)`); only SHA-256 hashes are stored; a session is refused when revoked or expired. Read; existing tests `test_only_hashes_of_tokens_are_stored`, `test_a_mini_app_session_expires_after_twelve_hours`, `test_sign_out_revokes_the_session`.
- The cookie is `HttpOnly`, `Secure`, `SameSite=Lax`, path `/api`. A cookie session needs the CSRF token on every method other than GET, HEAD and OPTIONS, and the comparison is constant-time. No GET route changes data on the caller's behalf (the one write behind a GET, expiring stale ownership transfers, is not caller-controlled). Read; existing tests.
- A cookie session's token is refused as a bearer token and the reverse. Read; existing test `test_session_kinds_are_not_interchangeable`.
- Mini App data and login data are verified with the correct key derivations, over every field except `hash`, with duplicate fields refused and age checked after the signature. Data of one kind cannot be used as the other. Read against Telegram's published algorithm; existing tests in `tests/test_telegram_auth.py`.
- Without a bot token no API is served at all; the webhook is refused at start with a secret shorter than 16 characters. Read; existing `tests/test_wiring.py`.
- The front end keeps the session token in memory only, sends the launch string once in a request body, and never puts either in storage or an address. Read; search of `frontend/src` found `localStorage` used for the language choice only.

**Authorization**

- Every tenant transaction in every service begins with `require_member`, or, for customer operations, follows `resolve_link` (the database function `my_link` with the caller's identifier). Checked by listing the statements after every `storage.tenant(` in `application/` and `interface/`. The exceptions are deliberate and safe: shop creation (the shop does not exist yet; its identifier is derived from the caller), the chat's lookups inside shops returned by `my_memberships` (each followed by `require_member` before any write), and the worker.
- Every `/api/` route is bound to a registered operation and every operation is exercised by the suite. Existing tests `test_every_api_route_is_a_registered_operation`, `test_every_operation_is_described_in_the_suite`.
- An identifier of another shop sent in a request body (member, customer, catalog item) is answered exactly like a random one and changes neither shop. The existing suite covered identifiers in the path only. New test `test_another_shops_identifier_in_a_body_is_answered_like_one_that_does_not_exist`, with `test_the_same_bodies_succeed_with_the_shops_own_identifiers` showing the refusals come from the foreign identifier.
- A removed member is answered 404 and lists no shops. New test `test_a_removed_member_reaches_nothing_of_the_shop`. A suspended member: existing suite.
- Outsiders get the same 404 body for an existing shop, a missing shop and a malformed identifier; a member with too low a role gets 403 before any record is looked up, so 403 against 404 says nothing about records. Existing test `test_refusals_are_indistinguishable_from_a_missing_shop`; read.
- In a suspended shop every data read and every data write except those of finding 5 is refused for managers and sellers, and writes for the owner. Probe of 55 shop operations as three roles (see "What was run").
- A customer reaches only the account behind their own live link; disputes, date requests and removal are narrowed to that customer's entries inside the shop. Read; existing `tests/api/test_customer_account.py`, `tests/db/test_customer_functions.py`.
- Idempotent replay returns a stored answer only after `require_member` has passed again, and only for the same operation, user and request. Tokens (invitation, personal link, counter code) are removed from the stored copy. Read.

**Tenant isolation in the database**

- Every table with a `shop_id` column except `outbox_message` has row-level security enabled and forced, with one policy that applies to reads and writes; `qd_app` is `NOBYPASSRLS`. Read; existing tests `test_every_table_with_a_shop_column_is_protected`, `test_app_role_cannot_bypass_row_level_security`.
- The tenant is set with `set_config('qd.shop_id', ..., true)` inside the transaction, so it cannot survive on a pooled connection. Read (`db.py`, class `Database`); existing `tests/db/test_tenant_context.py`.
- SQL is composed only from module constants and bound parameters; search text is escaped for `LIKE`. Read; existing `tests/test_sql_composition.py`.
- All 14 `SECURITY DEFINER` functions name a search path and are closed to `PUBLIC`. New test `test_every_definer_function_pins_its_search_path_and_is_closed_to_public`, with `test_the_definer_check_catches_a_function_declared_carelessly` showing the check detects a careless one. Each function was read: each takes the acting user or a token hash, returns identifiers and what its name says, and checks state itself (invitation issued and unexpired, link live, shop not erased). What they do not withstand is in findings 2 and 3.
- `qd_app` cannot update or delete entries, goods lines, promises or activity directly. Existing tests; new `test_the_application_role_cannot_delete_an_entry_directly`.

**Webhook and chat**

- The secret is compared in constant time before the body is read; an update is claimed once by its identifier in the same transaction as its replies. Read; existing `tests/api/test_webhook.py`.
- Only private chats where the sender is the chat are served. Read.
- Callback data carries an action and an identifier. Every action re-derives the shop from the caller's own memberships or links and authorizes again; a customer to be charged is picked by position in a list stored on the server for that user, never by an identifier in the button. A pending question is read and consumed only together with the user's identifier. Read (`chat.py`).
- Messages are sent as plain text (no `parse_mode`), so names, notes and reasons cannot inject markup; free text is whitespace-collapsed, length-limited and quoted. Read (`telegram_sender.py`, `domain/disputes.py`).

**Input handling**

- Lists are capped at 100 a page with opaque cursors that are validated; amounts, quantities, names, notes, reasons and line counts are bounded. Read. (The one unbounded input is the body itself: finding 4.)
- `/healthz` reveals only up or down; the API description endpoints are switched off. Read.

**Front end, dependencies, configuration**

- No `dangerouslySetInnerHTML`, no `innerHTML`, no `eval`, no redirect built from input; the only external address built is `https://t.me/<bot>?start=<token>`; QR codes are drawn in the browser. Search of `frontend/src`.
- The development-only preview (`?role=...`) is guarded by `import.meta.env.DEV` and grants nothing on the server. Read.
- No secret in the repository or its history; `.env` is ignored. Search described above.
- Rehearsal stack: loopback ports only, generated passwords, no fixed secret in the files. Read.

**Pull request 36**

- The upload route checks whose link it is before reading the body, then reads at most 5 MB plus 16 KB, counting bytes rather than trusting the declared length. Declared file names and types are ignored.
- Object keys are 256 random bits, carry nothing about the shop or person, and are validated before every store call; the filesystem store also checks the resolved path stays inside its root.
- A file is reached only through its row, read inside the shop's tenant transaction after `require_member`; another shop's file identifier finds nothing. Content is compared with its stored SHA-256 before being served. The download name is generated by the server.
- The S3 client signs requests itself, follows no redirects and reads no proxy from the environment; error texts carry no content.

All four by reading the diff.

## Not reviewed

- The administration API and second factor (story S18.1): no pull request exists. A local branch `feat/s18-1-admin` exists and was not read.
- Payment provider endpoints (`/pay/click`, `/pay/payme`): not on `main`.
- Imports and exports: not on `main`.
- Any deployed environment, proxy, TLS, headers, host hardening, backups and their encryption key handling: nothing exists to review (note 14).
- Behaviour with a real Telegram client: sign-in was verified with generated signatures only, as `PROGRESS.md` already states.
- In `infrastructure/db.py`, the bodies of the tenant queries between the customer queries and the platform session (about 1,000 lines: catalog, links, disputes, reminders, reports, date requests) were not read line by line; they were covered by the composition test and by the tenant suite only.
- `application/catalog.py`, `reminders.py`, `reports.py`, `subscription.py`, `shop_deletion.py`, `notify.py`, `removal.py`, `dispatch.py`, `scheduler.py`: only their authorization preamble was checked. What customer notifications contain was not compared with the consent text.
- The 129 lines pull request 38 added to `chat.py`, and its date-request rules beyond the authorization and locking pattern.
- Pull request 36: its chat changes, its changes to `db.py`, and its tests. Pull request 40: everything except the sign-in files. Pull request 39: its tests.
- Front-end screens beyond the entry points, the transport and the searches listed.
- Concurrency (two requests racing on one record), timing side channels, and denial of service beyond findings 4, 8, 13, P36-2 and P39-1.
- Correctness of balances, allocation and reports.

## The authorization table: specification against the suite

Compared: the "Authentication / authorization" table and the "Resources" table of the technical specification with the hand-written `ALLOWED_ROLES` in `backend/tests/api/test_authorization_suite.py` at `1bf48b9` (59 shop operations). `test_the_code_agrees_with_the_hand_written_table` compares only the lowest allowed role of each operation with the code; that is enough while roles are strictly ordered, and would miss an error if they ever stop being so.

Rows not listed here agree with the specification.

| Operation | Specification | Suite and code | Difference |
|---|---|---|---|
| `counter_code.rotate` | `/counter-code`: all staff | Manager, owner | Stricter than specified. The suite gives a reason (the printed code is invalidated). Record it in the specification. |
| `shop.credit.update` (default limit, whether sellers may exceed it) | "Shop settings": owner. "Edit customers and limits": manager, owner | Manager, owner | The specification can be read either way. As implemented, a manager can switch on "sellers may exceed" for the whole shop. Decide and write it down. |
| `shop.credit.read` | Not listed | All staff | Addition, with a stated reason (a seller must know the rule they sell under). |
| `ownership.transfer.read`, `.accept`, `.decline` | "Staff, ownership": owner | Manager, owner; inside the operation only the named manager may answer | Necessary: the person receiving the shop is a manager. The specification's table does not say so. A manager who is not the target can read that a transfer is pending and to whom. |
| `ledger.entry.promise.choose` | Not in the table. "Change promised date": manager, owner | All staff by role; inside the operation only the entry's author or a manager, once, within 24 hours, while the date is still the shop default | Addition from REQ-008. The later change (`ledger.entry.promise.change`) is manager and owner, as specified. |
| `ledger.entry.lines.add` | "Author, manager, owner" | All staff by role; the author rule is inside the operation | Agrees in effect. The suite's table alone does not show the author rule; other tests do. |
| `customers.update` covering the credit limit and reminders on or off | "Other changes: manager, owner" | Manager, owner | Agrees. Noted because the limit and the reminder switch are fields of this one operation, not operations of their own. |
| `me.owner_totals` | Owner | Any signed-in user ("self" scope); the service includes only shops where the caller is the active owner | Agrees in effect: a non-owner gets an empty list, not a refusal. |
| `disputes.list`, `disputes.decline` | "Disputes: manager, owner" | Manager, owner | Agrees. There is no "accept" operation: accepting a dispute is reversing the entry. |
| Limited and suspended modes | "In limited mode the credit-sale and import capabilities are refused for every role; in suspended mode only owner viewing and export remain" | Not part of `ALLOWED_ROLES`; covered by tests of single operations | The suite does not run the operations against a suspended or limited shop. Doing so found finding 5. Add both modes to the suite as further callers. |
| Customer capabilities (view, dispute, payment notice, date request, disconnect, removal) | Customer, own account | "Self" operations, checked with a link nobody holds (404) | The suite proves a stranger is refused, not that a customer is confined to their own account; that is in `tests/api/test_customer_account.py` and `tests/api/test_date_requests.py`. |
| Payment notices (list, accept, decline, receipt) | All staff | Not on `main`. Pull request 36: all staff | Agrees, when merged. |
| Exports, imports, activity filters by export, administrator rows, "read a shop's data only with open support access" | Specified | No operation exists | Not implemented yet, so not checked. The suite does include a platform administrator without support access among the outsiders, and that caller is refused everywhere. |

## Which findings to settle before a real shop is onboarded

The reviewer's opinion, for the founder to decide:

- Findings 1, 2, 3 and 4, and 8 together with P39-1: each is a control the documents promise and the code does not deliver.
- Finding 7, because it concerns personal data leaving the database for log files, and finding 5, because suspension is the administrator's only means against a misused shop.
- Note 14 is not a finding against the code, but most of the "Security" table of the specification lives in the deployment, which does not exist yet and must be reviewed when it does.
- The pull request findings (P36-1 to P36-3, P39-1, P39-2) are best settled in those pull requests before they are merged.
