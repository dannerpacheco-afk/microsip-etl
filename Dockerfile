# Multi-stage Dockerfile for the Microsip ETL pipeline
# =====================================================
# Usage:
#   docker build -t microsip-etl .
#   docker run --env-file .env microsip-etl [nightly|catalogs|facts|backfill ...]

FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies in a separate layer for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and SQL views
COPY *.py ./
COPY sql ./sql

# Run as non-root user for security
RUN groupadd --gid 1000 etl && \
    useradd --uid 1000 --gid etl --no-create-home etl
USER etl

ENTRYPOINT ["python", "main.py"]
CMD ["nightly"]
