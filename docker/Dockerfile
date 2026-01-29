# Build stage
FROM python:3.11-slim as builder

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# Runtime stage
FROM python:3.11-slim

WORKDIR /app

# Install dependencies from wheels
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# Copy application
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

# Simulator fixtures (scenario registry + schema)
COPY fixtures/ ./fixtures/

COPY docker/docker-entrypoint.sh ./

RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]