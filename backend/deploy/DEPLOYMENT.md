# Backend Deployment — Oracle Cloud VM (no registry)

This deploys the backend stack on an OCI Compute VM **without OCIR** (no registry
cost). GitHub Actions SSHes into the VM, pulls the latest code, builds the image
locally, and recreates only the backend container.

Stack:
- FastAPI backend container (built on the VM)
- ChromaDB container
- Nginx reverse proxy container (port 80 → backend:8000)

## 0) OCI credentials you actually need

Because we skip OCIR, the deploy pipeline needs **only SSH access** to the VM:
nothing from OCI's API, no Auth Token, no Namespace.

- `OCI_VM_HOST` — the VM's public IP (or DNS)
- `OCI_VM_USER` — e.g. `ubuntu`
- `OCI_VM_SSH_KEY` — the **private** key (PEM) whose public half is on the VM
- `OCI_VM_SSH_PORT` — optional, default `22`

> You still need an OCI account to *create* the VM, but those tenancy/user OCIDs
> are not used by the deploy pipeline.

## 1) Create the VM (OCI Console)

Recommended (free-tier eligible):
- **Image:** Ubuntu **22.04 LTS** (aarch64). (24.04 also works; 22.04 is safest
  for the torch/spacy ARM wheels.)
- **Shape:** `VM.Standard.A1.Flex` (ARM Ampere) — choose **2 OCPUs / 12 GB RAM**.
  (Always-Free cap is 4 OCPUs / 24 GB total in the tenancy.)
- **Networking:** assign a **public IP**; create/attach a VCN.
- **SSH:** paste your **public** key (generate with `ssh-keygen -t ed25519`).
- **Security Lists / Ingress:** allow `22/tcp` (SSH) and `80/tcp` (nginx).
  Optionally `8001/tcp` if you want direct backend access.
- Note the **public IP** — that is `OCI_VM_HOST`.

> Boot volume is 200 GB free-tier, plenty for the ~10–11 GB image + Chroma data.

## 2) One-time setup on the VM

```bash
ssh ubuntu@<VM_IP>

# install docker + compose plugin
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER   # re-login after this

# clone the (public) repo
sudo git clone https://github.com/amolrathod7875/Vidhoor.git /opt/vidhoor
sudo chown -R $USER:$USER /opt/vidhoor
```

`.env`, `wallet/`, and `data/` are **not in git** — copy them from your machine
once (and again if they change):

```bash
# from your local machine
scp backend/.env ubuntu@<VM_IP>:/opt/vidhoor/backend/.env
scp -r backend/wallet ubuntu@<VM_IP>:/opt/vidhoor/backend/wallet
scp -r backend/data  ubuntu@<VM_IP>:/opt/vidhoor/backend/data
```

First deploy (builds the image on the VM; ~15–40 min on first run, fast after):

```bash
cd /opt/vidhoor/backend
bash deploy/deploy_backend_oci.sh
```

## 3) GitHub Actions auto-deploy

The workflow `.github/workflows/backend-auto-deploy.yml` runs on push to `main`
(backend changes). It SSHes into the VM and does:

1. `git fetch --all && git reset --hard origin/main`
2. `docker compose build backend`
3. `docker compose up -d --no-deps --force-recreate backend`
4. prune images older than 7 days

Add these **repository secrets** (Settings → Secrets → Actions):
- `OCI_VM_HOST`
- `OCI_VM_USER`
- `OCI_VM_SSH_KEY` (private key PEM content)
- `OCI_VM_SSH_PORT` (optional, default `22`)

> `git reset --hard` only touches tracked files; your untracked `.env`, `wallet/`,
> and `data/` stay intact on the VM.

## 4) Verify

```bash
curl http://<VM_IP>/
docker compose -f deploy/docker-compose.oci.yml ps
```

If the security list allows ingress on `80`, open `http://<VM_IP>/` in a browser.

## 5) Frontend (Vercel)

- `frontend/vercel.json` rewrites `/api` and `/legal` to the backend. Update the
  hardcoded IP `161.118.160.239` → your new VM public IP, then redeploy on Vercel.
- Optionally set Vercel env `VITE_API_BASE_URL` to `http://<VM_IP>`.

## Notes
- Nginx listens on `80` and proxies to backend on `8000` internally.
- Chroma data persists in the Docker volume `chroma_data`.
- Image is built for `linux/arm64` (A1 shape). If you ever use an x86 shape
  (`VM.Standard.E2.1.Micro`), rebuild natively on that VM (no cross-arch needed).
