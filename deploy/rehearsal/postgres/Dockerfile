# PostgreSQL 16 with pgBackRest, for the local rehearsal only.
# The official image already carries the PGDG apt source, so pgBackRest comes from there.
FROM postgres:16-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends pgbackrest jq \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o postgres -g postgres -m 750 \
        /var/lib/pgbackrest /var/log/pgbackrest /var/spool/pgbackrest /fence

COPY pgbackrest.conf /etc/pgbackrest/pgbackrest.conf
COPY --chmod=755 qd-entrypoint.sh /usr/local/bin/qd-entrypoint.sh
COPY --chmod=755 10-rehearsal-init.sh /docker-entrypoint-initdb.d/10-rehearsal-init.sh

ENTRYPOINT ["qd-entrypoint.sh"]
CMD ["postgres"]
