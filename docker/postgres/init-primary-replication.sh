#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
DO \$\$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = '${POSTGRES_REPLICATION_USER}'
    ) THEN
        CREATE ROLE ${POSTGRES_REPLICATION_USER}
            WITH REPLICATION LOGIN PASSWORD '${POSTGRES_REPLICATION_PASSWORD}';
    END IF;
END
\$\$;
EOSQL

echo "host replication ${POSTGRES_REPLICATION_USER} all md5" >> "$PGDATA/pg_hba.conf"
