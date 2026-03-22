# Chroma persistence (survive restart)

Your embeddings were disappearing because Chroma was not running with a persistent volume.

## Recommended (Docker, persistent)

From `backend/` run:

```powershell
docker compose -f docker-compose.chroma.yml up -d
```

This stores vectors in `backend/chroma/`, so rebooting your laptop will **not** wipe embeddings.

## One-command startup (recommended)

From repo root run:

```powershell
./start_all.ps1
```

This will:
- start persistent Chroma (`docker compose`),
- start backend on `127.0.0.1:8001`,
- start frontend dev server.

Optional flags:

```powershell
./start_all.ps1 -SkipChroma
./start_all.ps1 -SkipFrontend
```

## Stop / start

```powershell
docker compose -f docker-compose.chroma.yml stop
# later
docker compose -f docker-compose.chroma.yml start
```

## Re-ingest only once (or when data changes)

```powershell
$env:PYTHONPATH='d:\vidhoor-legal-copilot\backend'
python backend\ingest_legal_resources.py --input-dir backend\data --host 127.0.0.1 --port 8000 --status active
```

## Backend connection

`backend/main.py` now reads:
- `CHROMA_HOST` (default `127.0.0.1`)
- `CHROMA_PORT` (default `8000`)

So you can point backend to remote Chroma later without code changes.

## Do you need Oracle Cloud right now?

Not required for persistence. Local Docker volume is enough for dev.

Use Oracle Cloud only if you need:
- always-on multi-user environment,
- remote team access,
- production-grade uptime/backup/network policies.
