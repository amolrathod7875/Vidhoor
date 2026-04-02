#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${OCI_REGION:-}" || -z "${OCI_NAMESPACE:-}" || -z "${OCI_AUTH_TOKEN:-}" ]]; then
  echo "Missing OCI auth vars. Required: OCI_REGION, OCI_NAMESPACE, OCI_AUTH_TOKEN"
  exit 1
fi

if [[ -z "${OCI_USERNAME:-}" && -z "${OCI_REGISTRY_USERNAME:-}" ]]; then
  echo "Missing username. Set OCI_USERNAME or OCI_REGISTRY_USERNAME"
  exit 1
fi

if [[ -z "${BACKEND_IMAGE:-}" ]]; then
  echo "BACKEND_IMAGE not set. Example: us-ashburn-1.ocir.io/namespace/vidhoor-backend:latest"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

echo "Logging in to OCIR..."
if [[ -n "${OCI_REGISTRY_USERNAME:-}" ]]; then
  REGISTRY_USERNAME="${OCI_REGISTRY_USERNAME}"
elif [[ "${OCI_USERNAME}" == */* ]]; then
  REGISTRY_USERNAME="${OCI_USERNAME}"
else
  REGISTRY_USERNAME="${OCI_NAMESPACE}/${OCI_USERNAME}"
fi

echo "${OCI_AUTH_TOKEN}" | docker login "${OCI_REGION}.ocir.io" -u "${REGISTRY_USERNAME}" --password-stdin

echo "Pulling and recreating backend service only..."
docker compose -f deploy/docker-compose.oci.yml pull backend
docker compose -f deploy/docker-compose.oci.yml up -d --no-deps --force-recreate backend

echo "Pruning stale images to save disk..."
docker image prune -af --filter "until=168h" || true

echo "Deployment complete."
docker compose -f deploy/docker-compose.oci.yml ps
