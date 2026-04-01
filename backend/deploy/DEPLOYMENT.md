# Backend Deployment (Oracle Cloud + Docker)

This guide deploys only the backend stack:

- FastAPI backend container
- ChromaDB container
- Nginx reverse proxy container

## 1) Build and push backend image to OCIR

From your local machine:

```bash
cd backend
export OCI_REGION=us-ashburn-1
export OCI_NAMESPACE=<your-namespace>
export OCI_USERNAME=<your-ocir-username>
export OCI_AUTH_TOKEN=<your-ocir-auth-token>
export IMAGE_REPO=vidhoor-backend
export IMAGE_TAG=latest

bash deploy/build_and_push_ocir.sh
```

## 2) Prepare backend environment on OCI host

On the OCI VM/instance, place backend code at a path like `/opt/vidhoor/backend` and create:

- `.env` (copy from `.env.example` and fill values)
- `wallet/` folder with Oracle wallet files (if Oracle DB uses wallet)

Required minimum:

- `CEREBRAS_API_KEY`
- `ORACLE_USER`
- `ORACLE_PASSWORD`
- `ORACLE_DSN`
- `ORACLE_WALLET_PASSWORD` (if wallet auth enabled)

## 3) Deploy backend stack on OCI host

```bash
cd /opt/vidhoor/backend
export OCI_REGION=us-ashburn-1
export OCI_NAMESPACE=<your-namespace>
export OCI_USERNAME=<your-ocir-username>
export OCI_AUTH_TOKEN=<your-ocir-auth-token>
export BACKEND_IMAGE=us-ashburn-1.ocir.io/<your-namespace>/vidhoor-backend:latest

bash deploy/deploy_backend_oci.sh
```

## 4) Verify

```bash
docker compose -f deploy/docker-compose.oci.yml ps
curl http://127.0.0.1/
```

If security lists allow ingress on `80`, test from browser:

- `http://<oci-public-ip>/`

## 5) Update/redeploy

1. Build and push new image tag.
2. Update `BACKEND_IMAGE`.
3. Run `bash deploy/deploy_backend_oci.sh` again.

## 6) Optional: Automatic deploy on `git push`

This repo now includes workflow:

- `.github/workflows/backend-auto-deploy.yml`

What it does on push to `main` (backend changes):

1. Builds backend image for `linux/arm64`
2. Pushes to OCIR (`latest` + commit SHA tag)
3. SSHes into OCI VM and runs `deploy/deploy_backend_oci.sh`

Configure these GitHub repository secrets:

- `OCI_REGION` (example: `ap-mumbai-1`)
- `OCI_NAMESPACE` (example: `bmerfrkjsskj`)
- `OCI_REGISTRY_USERNAME` (full OCIR username, example: `bmerfrkjsskj/amolrathod7875470402@gmail.com`)
- `OCI_AUTH_TOKEN` (OCI auth token)
- `OCI_VM_HOST` (public IP or DNS of VM)
- `OCI_VM_USER` (example: `ubuntu`)
- `OCI_VM_SSH_KEY` (private key content for the VM, PEM format)
- `OCI_VM_SSH_PORT` (optional, default `22`)

Notes:

- VM path is assumed as `/opt/vidhoor/backend`.
- Ensure updated `deploy/` scripts from this repo are present on VM.
- If VM does not pull latest repo automatically, manually sync backend folder once after adding this workflow.

## Notes

- In this setup, Nginx listens on `80` and proxies to backend on `8000` internally.
- API remains available under `/api/...` via Nginx.
- Chroma data is persisted in Docker volume `chroma_data`.
