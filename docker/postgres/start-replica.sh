#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$PGDATA"
    chown -R postgres:postgres "$PGDATA"
    exec gosu postgres "$0"
fi

if [ ! -s "$PGDATA/PG_VERSION" ]; then
    rm -rf "$PGDATA"/*
    until pg_isready -h "$POSTGRES_PRIMARY_HOST" -p "${POSTGRES_PRIMARY_PORT:-5432}" -U "$POSTGRES_USER"; do
        sleep 2
    done

    PGPASSWORD="$POSTGRES_REPLICATION_PASSWORD" pg_basebackup \
        -h "$POSTGRES_PRIMARY_HOST" \
        -p "${POSTGRES_PRIMARY_PORT:-5432}" \
        -D "$PGDATA" \
        -U "$POSTGRES_REPLICATION_USER" \
        -Fp \
        -Xs \
        -P \
        -R
fi

exec docker-entrypoint.sh postgres
