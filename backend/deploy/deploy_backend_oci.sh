#!/usr/bin/env bash
set -euo pipefail

# No registry: build the image locally on the OCI VM and recreate only the
# backend service. Keeps everything inside the VM (no OCIR cost).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

echo "Building backend image locally (no registry)..."
docker compose -f deploy/docker-compose.oci.yml build backend

echo "Recreating backend service..."
docker compose -f deploy/docker-compose.oci.yml up -d --no-deps --force-recreate backend

echo "Pruning stale images to save disk..."
docker image prune -af --filter "until=168h" || true

echo "Deployment complete."
docker compose -f deploy/docker-compose.oci.yml ps
