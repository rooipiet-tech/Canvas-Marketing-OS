#!/bin/sh
# Canvas Marketing OS — pgbouncer/entrypoint.sh
#
# Renders pgbouncer.ini + userlist.txt from env vars (Container Apps
# secrets, never baked into the image) and execs pgbouncer. This repo has
# exactly one Postgres role in practice — every service's DATABASE_URL is
# built from the SAME administratorLogin/administratorLoginPassword (see
# infra/main.bicep and every *-app.bicep/*-job.bicep's databaseUrl var) —
# so one [databases] wildcard entry and one userlist.txt line cover every
# real caller; there is no per-service credential to fan out here.
#
# POOL_MODE=session, not the more aggressive "transaction" mode, and this is
# load-bearing, not a default left untouched:
#   - services/orchestrator/orchestrator/db.py's try_advisory_lock /
#     release_advisory_lock hold a SESSION-scoped pg_advisory_lock across
#     multiple separate statements/transactions on one held connection (the
#     QA retry loop's mutual-exclusion primitive) — transaction pooling would
#     let PgBouncer hand that backend connection to a different client
#     between the lock and unlock calls, silently breaking the mutual
#     exclusion this exists for.
#   - services/model-gateway/caching.py's PostgresCache does the identical
#     thing over services/model-gateway/db.py's psycopg2 pool, across an
#     await boundary.
# Session mode makes PgBouncer transparent to both: a client's backend
# connection is dedicated to it for the life of its own connection to
# PgBouncer, exactly like a direct Postgres connection. What PgBouncer still
# buys under session mode is the part TD-12 actually needs: DEFAULT_POOL_SIZE
# is the real, PgBouncer-enforced ceiling on backend connections, so a
# service's own pool size no longer has to be hand-tuned against
# max_connections × replica-count math to avoid exhausting the server — extra
# client connections queue at PgBouncer instead of failing at Postgres.
set -eu

: "${POSTGRES_HOST:?POSTGRES_HOST is required}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_DB:=postgres}"
: "${POSTGRES_ADMIN_USER:?POSTGRES_ADMIN_USER is required}"
: "${POSTGRES_ADMIN_PASSWORD:?POSTGRES_ADMIN_PASSWORD is required}"
: "${PGBOUNCER_LISTEN_PORT:=6432}"
: "${PGBOUNCER_POOL_MODE:=session}"
: "${PGBOUNCER_MAX_CLIENT_CONN:=1000}"
: "${PGBOUNCER_DEFAULT_POOL_SIZE:=25}"
: "${PGBOUNCER_MIN_POOL_SIZE:=5}"

USERLIST=/etc/pgbouncer/userlist.txt
INI=/etc/pgbouncer/pgbouncer.ini

# Plaintext password in userlist.txt is a supported PgBouncer form for both
# md5 and scram-sha-256 auth_type — PgBouncer computes the SCRAM/md5
# exchange itself from it. Written at container startup from a Container
# Apps secret env var, never baked into the image or committed anywhere.
umask 077
printf '"%s" "%s"\n' "$POSTGRES_ADMIN_USER" "$POSTGRES_ADMIN_PASSWORD" > "$USERLIST"

cat > "$INI" <<EOF
[databases]
* = host=$POSTGRES_HOST port=$POSTGRES_PORT dbname=$POSTGRES_DB user=$POSTGRES_ADMIN_USER password=$POSTGRES_ADMIN_PASSWORD

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = $PGBOUNCER_LISTEN_PORT
auth_type = scram-sha-256
auth_file = $USERLIST
pool_mode = $PGBOUNCER_POOL_MODE
max_client_conn = $PGBOUNCER_MAX_CLIENT_CONN
default_pool_size = $PGBOUNCER_DEFAULT_POOL_SIZE
min_pool_size = $PGBOUNCER_MIN_POOL_SIZE
server_tls_sslmode = require
admin_users = $POSTGRES_ADMIN_USER
EOF

exec pgbouncer "$INI"
