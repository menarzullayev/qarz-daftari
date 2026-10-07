# Tools for the local proof of deploy/backup only: rclone for the file store scripts, shellcheck and
# systemd-analyze for the checks. The user has the same number as postgres in the database image, so
# both can write the shared state volume.
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates jq openssl rclone shellcheck systemd \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -o -g 999 postgres \
    && useradd -o -u 999 -g 999 -d /var/lib/postgresql -m postgres \
    && install -d -o postgres -g postgres /var/lib/qarz-backup \
    && ln -s /qd-backup /opt/qarz-backup
