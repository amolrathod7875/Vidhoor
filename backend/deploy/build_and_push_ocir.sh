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

IMAGE_REPO="${IMAGE_REPO:-vidhoor-backend}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
IMAGE_URI="${OCI_REGION}.ocir.io/${OCI_NAMESPACE}/${IMAGE_REPO}:${IMAGE_TAG}"

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

echo "Building image ${IMAGE_URI}..."
docker build -t "${IMAGE_URI}" .

echo "Pushing image ${IMAGE_URI}..."
docker push "${IMAGE_URI}"

echo "Done."
echo "Set BACKEND_IMAGE=${IMAGE_URI} on your OCI host before running deploy_backend_oci.sh"
