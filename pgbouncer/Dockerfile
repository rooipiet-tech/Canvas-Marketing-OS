# Canvas Marketing OS — pgbouncer/Dockerfile
#
# TD-12 fix: a single shared PgBouncer instance in front of the one Postgres
# Flexible Server, for the 6 long-running services (ca-model-gateway,
# ca-gatekeeper, ca-gatekeeper-approval, ca-publisher, ca-vault,
# ca-orchestrator) that hold persistent connection pools and can each scale
# to 3 replicas. See infra/modules/pgbouncer-app.bicep and entrypoint.sh for
# the pooling-mode rationale.
#
# A stock Debian package rather than a third-party Docker Hub image, for the
# same reason every other data-plane component in this repo builds its own
# image from a pinned base: an external image name/tag is a hypothesis about
# its own behaviour (L-0026/L-0068/L-0078) this repo doesn't get to verify
# before a real deploy, where a stock apt package + a startup script we
# wrote and can read in full is not.

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends pgbouncer ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /etc/pgbouncer && chown -R postgres:postgres /etc/pgbouncer

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER postgres
EXPOSE 6432
ENTRYPOINT ["/entrypoint.sh"]
