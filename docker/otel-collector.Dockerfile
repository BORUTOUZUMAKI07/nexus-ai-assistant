# Layer-2 collection agent for Nexus AI Assistant — deployable as a Render web
# service (render.yaml) or any Docker host. Pinned to a tag validated against
# this repo's config (0.121.0 supports `otelcol validate`).
FROM otel/opentelemetry-collector-contrib:0.121.0

# Default config lives beside this Dockerfile.
COPY otel-collector-config.yaml /etc/otelcol-contrib/config.yaml

# OTLP/HTTP :4318 (what the app pushes logs to / Render maps 443→$PORT),
# OTLP/gRPC :4317, self-metrics :8888, health :13133.
EXPOSE 4317 4318 8888 13133