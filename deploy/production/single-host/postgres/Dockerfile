# syntax=docker/dockerfile:1
# The database image of the single-host deployment (compose.single-host.yml): PostgreSQL 16 with
# pgBackRest, and the tools of the backup jobs. One image for four uses:
#   db                 PostgreSQL, archiving its write-ahead log to the S3 repository (Cloudflare R2)
#   backup             the scheduler: backups, expiry, the copy of the stored files, the restore test
#   restore, ...       one-shot commands started by scripts/single-host.sh
#   db-scratch         a point-in-time copy beside the live database, never over it
# Build context: deploy/  (it takes deploy/backup/scripts as they are, and production/single-host/).
#
# The official image carries the PGDG apt source, so pgBackRest comes from there (2.59 at the time of
# writing). rclone is Debian's (1.60): S3 with path-style addressing and the `crypt` overlay are all
# that is used of it.
FROM postgres:16.15-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl jq openssl pgbackrest rclone \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o postgres -g postgres -m 750 /var/lib/qarz-backup \
    && install -d -o 10001 -g 10001 -m 755 /var/lib/qarz /var/lib/qarz/files /var/lib/qarz-files-backup \
    && rm -f /etc/pgbackrest.conf
# About the line with 10001 above: the stored files and the state of their copy belong to the
# application's user, exactly as in the backend image. Docker gives an empty volume the owner and mode
# of the directory it is mounted over, whichever container mounts it, so the two images must agree.

COPY production/single-host/postgres/pgbackrest.conf /etc/pgbackrest/pgbackrest.conf
# The scripts written for the two-server design, unchanged: a backup, the restore test, expiry, the
# check and the monthly dump do not care where the repository is.
COPY --chmod=755 backup/scripts/ /opt/qarz-backup/scripts/
COPY --chmod=755 production/single-host/scripts/ /opt/qarz-single/
COPY --chmod=755 production/single-host/postgres/qd-entrypoint.sh /usr/local/bin/qd-entrypoint.sh

ENV TZ=Asia/Tashkent
# Never root: the data directory, the socket directory and the state directory belong to postgres.
USER postgres
ENTRYPOINT ["qd-entrypoint.sh"]
CMD ["postgres"]
