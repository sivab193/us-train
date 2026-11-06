# ---- build stage ----
FROM rust:1.82-slim AS builder
WORKDIR /app

# Build deps: openssl not needed (rustls), but pkg-config/ca-certs help.
RUN apt-get update && apt-get install -y --no-install-recommends \
        pkg-config ca-certificates && rm -rf /var/lib/apt/lists/*

# Cache dependencies: copy manifests, build a dummy, then the real sources.
COPY Cargo.toml ./
RUN mkdir src && echo "fn main() {}" > src/main.rs \
    && cargo build --release || true
RUN rm -rf src

COPY src ./src
# Touch main so cargo rebuilds with the real sources.
RUN touch src/main.rs && cargo build --release

# ---- runtime stage ----
FROM debian:bookworm-slim AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/train-api /usr/local/bin/train-api

ENV API_PORT=8080
EXPOSE 8080

# Non-root
RUN useradd -r -u 10001 appuser
USER appuser

CMD ["train-api"]
