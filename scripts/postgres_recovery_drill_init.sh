#!/bin/sh
set -eu

hba_rule='host replication officeagent_drill_replication all scram-sha-256'
if ! grep -Fqx "$hba_rule" "$PGDATA/pg_hba.conf"; then
    printf '%s\n' "$hba_rule" >> "$PGDATA/pg_hba.conf"
fi

psql \
    --set ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set replication_password="$POSTGRES_PASSWORD" \
    <<'SQL'
CREATE ROLE officeagent_drill_replication LOGIN REPLICATION
    PASSWORD :'replication_password';
SQL
