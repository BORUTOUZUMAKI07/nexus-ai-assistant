# Layer-2 collection agent for Nexus AI Assistant — deployable as a Render web
# service (render.yaml) or any Docker host. Pinned to a tag validated against
# this repo's config (0.121.0 supports `otelcol validate`).
FROM busybox:stable AS queuedir
# The durable queue directory for file_storage. Created here (root, with a
# shell) and copied in with the collector uid below, because the collector
# image is distroless (no shell) and `create_directory: true` cannot mkdir
# under an unwritable /var/lib. A fresh docker named volume mounted at this
# path inherits this ownership, so docker-compose works without a chown.
RUN mkdir -p /var/lib/otelcol/storage

FROM otel/opentelemetry-collector-contrib:0.121.0

# Default config lives beside this Dockerfile.
COPY otel-collector-config.yaml /etc/otelcol-contrib/config.yaml

# Durable file_storage queue directory, owned by the collector user (uid 10001).
COPY --from=queuedir --chown=10001:10001 /var/lib/otelcol /var/lib/otelcol

# OTLP/HTTP :4318 (what the app pushes to / Render maps 443→$PORT),
# OTLP/gRPC :4317, self-metrics :8888, health :13133.
EXPOSE 4317 4318 8888 13133
