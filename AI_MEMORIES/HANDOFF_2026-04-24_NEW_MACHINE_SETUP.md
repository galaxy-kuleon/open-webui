# Handoff: New-Machine Setup — Open WebUI + Hermes Agent + Honcho Stack

## Goal

Set up a complete **Open WebUI + Hermes Agent API + Honcho** stack on a brand-new machine using Docker Compose, cloning from our forks.

## Prerequisites

- Docker Engine >= 24.0
- Docker Compose >= 2.20
- `curl`, `git`
- An LLM provider API key (e.g., OpenRouter, OpenAI) for Honcho

## Directory Layout

All three repos live side-by-side. The compose file in `open-webui` references the other two via relative paths (`../hermes-agent`, `../honcho-deploy`).

```
~/workspace/
├── open-webui/          (our fork)
├── hermes-agent/        (our fork)
└── honcho-deploy/       (upstream deploy configs)
```

## Step 1: Clone Repositories

```bash
mkdir -p ~/workspace && cd ~/workspace

git clone https://github.com/galaxy-kuleon/open-webui.git
git clone https://github.com/galaxy-kuleon/hermes-agent.git
git clone https://github.com/plasticlabs/honcho-deploy.git honcho-deploy

cd open-webui
git checkout fix/hermes-e2e-selectors

cd ../hermes-agent
git checkout feat/kuleon-openwebui-identity
```

## Step 2: Configure Secrets

### 2.1 Open WebUI stack environment

```bash
cd ~/workspace/open-webui
cp .env.stack.example .env.stack
```

Edit `.env.stack` and fill in **real values**:

```bash
# Generate a strong secret for Open WebUI
WEBUI_SECRET_KEY=$(openssl rand -base64 32)

# Hermes API server key (must match between Hermes and Open WebUI)
API_SERVER_KEY=$(openssl rand -hex 32)
# Open WebUI presents this same key to Hermes
HERMES_API_KEY=${API_SERVER_KEY}

# Hermes upstream LLM provider key
OPENCODE_GO_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Honcho LLM provider key
LLM_OPENAI_API_KEY=sk-yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy
```

### 2.2 Hermes API server config

On first run the Hermes container bootstraps default config files into its volume. After the first start you must edit the generated config to enable the API server.

**Option A — Pre-seed config (recommended)**

Create the volume with config before starting:

```bash
docker volume create open-webui_hermes-data

# Bootstrap default files by running once
docker run --rm -v open-webui_hermes-data:/opt/data \
  nousresearch/hermes-agent:latest true

# Now edit the generated config
docker run --rm -v open-webui_hermes-data:/opt-data alpine sh -c \
  "sed -i 's/enabled: false/enabled: true/' /opt-data/config.yaml"
```

**Option B — Manual after first start**

After `docker compose up`, exec into the hermes container and edit `/opt/data/config.yaml`:

```yaml
platforms:
  api_server:
    enabled: true
    extra:
      host: 0.0.0.0
      port: 8642
```

Then restart the hermes service.

> **Important**: Hermes also needs `.env` inside its data volume with `API_SERVER_KEY` and `OPENCODE_GO_API_KEY`. The compose file injects these as container environment variables, which is sufficient for the API server key. The upstream provider key (`OPENCODE_GO_API_KEY`) is used by Hermes to call LLMs and is also passed via compose environment.

### 2.3 Honcho config

Honcho reads from `.env.stack` via `env_file`. No extra config needed unless you want to change models or enable auth.

## Step 3: Start the Stack

```bash
cd ~/workspace/open-webui
docker compose -f docker-compose.stack.yml up -d --build
```

This brings up **6 services**:

| Service | Purpose | Exposed Port |
|---------|---------|-------------|
| `open-webui` | Web UI + API | `127.0.0.1:8059` |
| `hermes` | Agent API server | `127.0.0.1:8642` |
| `honcho-api` | Memory / context API | `127.0.0.1:8000` |
| `honcho-deriver` | Background memory extraction | (internal) |
| `honcho-db` | PostgreSQL + pgvector | `127.0.0.1:5432` |
| `honcho-redis` | Cache | `127.0.0.1:6379` |

Wait for health checks (about 30–60 seconds):

```bash
sleep 30
docker compose -f docker-compose.stack.yml ps
```

## Step 4: Verify (Smoke Test)

### 4.1 Health endpoints

```bash
curl -sf http://127.0.0.1:8059/health
curl -sf http://127.0.0.1:8642/health
curl -sf http://127.0.0.1:8000/health
```

Expected:
- Open WebUI: `{"status":true}`
- Hermes: `{"status":"ok","platform":"hermes-agent"}`
- Honcho: HTTP 200

### 4.2 Hermes direct model auth

```bash
source .env.stack
curl -s http://127.0.0.1:8642/v1/models \
  -H "Authorization: Bearer ${API_SERVER_KEY}"
```

Should return a models list including `hermes-agent` variants.

### 4.3 Open WebUI auth & model discovery

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8059/api/v1/auths/signin \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke@test.com","password":"smoketest123"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))")

# If signup is needed (first run):
TOKEN=$(curl -s -X POST http://127.0.0.1:8059/api/v1/auths/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"smoke@test.com","password":"smoketest123","name":"Smoke"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token',''))")

curl -s "http://127.0.0.1:8059/api/models?refresh=true" \
  -H "Authorization: Bearer ${TOKEN}"
```

Should include `hermes_agent.hermes-agent` with capability metadata `{"delegated_orchestration": true}`.

### 4.4 End-to-end non-stream chat

```bash
curl -s -X POST http://127.0.0.1:8059/api/chat/completions \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"hermes_agent.hermes-agent",
    "messages":[{"role":"user","content":"Reply with exactly: ok"}],
    "stream":false,
    "max_tokens":16
  }'
```

Expected assistant content: `ok`

### 4.5 Streaming chat

```bash
curl -s -X POST http://127.0.0.1:8059/api/chat/completions \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"hermes_agent.hermes-agent",
    "messages":[{"role":"user","content":"Say hello"}],
    "stream":true
  }'
```

Should stream SSE chunks starting with `data: {...}`.

## Step 5: Register Admin User (First Run Only)

Open WebUI starts with `ENABLE_SIGNUP=true` and `DEFAULT_USER_ROLE=admin`. The first user to sign up becomes admin.

If you want to pre-seed the smoke-test user:

```bash
curl -X POST http://127.0.0.1:8059/api/v1/auths/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"adminpass123","name":"Admin"}'
```

Then disable signup in `.env.stack` and restart:
```bash
# Add to .env.stack
ENABLE_SIGNUP=false
```

```bash
docker compose -f docker-compose.stack.yml up -d
```

## Architecture Notes

### Network
All services communicate over the internal Docker bridge network `stack-net`. Service names resolve as hostnames:
- `http://hermes:8642` from Open WebUI
- `http://honcho-api:8000` from Hermes
- `http://honcho-db:5432` from Honcho services

### File Uploads
User-uploaded files land in the `uploads-data` named volume (mounted at `/app/backend/data/uploads` in Open WebUI). Hermes mounts this same volume **read-only** so the absolute paths injected by the Hermes pipe are valid inside the Hermes container.

### Delegated Orchestration
When `hermes_agent.hermes-agent` is selected, Open WebUI middleware **skips**:
- Local skill intercept
- File context injection
- Skip RAG injection
- RAG / knowledge injection

Hermes receives raw messages plus a system-message block containing file paths. Hermes handles its own skill routing, tool calling, memory recall, and file reading.

### Auth Separation
| Key | Role | Location |
|-----|------|----------|
| `API_SERVER_KEY` | Hermes API server auth | `.env.stack` |
| `HERMES_API_KEY` | Open WebUI → Hermes client auth | `.env.stack` (must match API_SERVER_KEY) |
| `OPENCODE_GO_API_KEY` | Hermes → upstream LLM provider | `.env.stack` |
| `LLM_OPENAI_API_KEY` | Honcho → LLM provider | `.env.stack` |

## Troubleshooting

### Hermes returns 401 Unauthorized
- Verify `API_SERVER_KEY` in `.env.stack` matches the key Hermes is using
- Check Hermes logs: `docker compose -f docker-compose.stack.yml logs hermes`
- Ensure `.hermes/.env` inside the Hermes volume has `API_SERVER_KEY` if env injection isn't working

### Open WebUI shows no Hermes models
- Check that `HERMES_API_KEY` is set in `.env.stack`
- Check Hermes health: `curl http://127.0.0.1:8642/health`
- Check Open WebUI logs for pipe instantiation errors

### File paths not resolving in Hermes
- Ensure `uploads-data` volume is shared between `open-webui` and `hermes`
- Hermes must mount it read-only at the same absolute path: `/app/backend/data/uploads`
- Check that Open WebUI's `UPLOAD_DIR` matches the mount point

### Honcho API fails to start
- Check DB health: `docker compose -f docker-compose.stack.yml logs honcho-db`
- Ensure `DB_CONNECTION_URI` uses `postgresql+psycopg` prefix
- Verify `.env.stack` is readable and contains required LLM keys

## Restart / Rebuild

Rebuild Open WebUI after code changes:
```bash
cd ~/workspace/open-webui
docker compose -f docker-compose.stack.yml up -d --build open-webui
```

Restart Hermes after config changes:
```bash
docker compose -f docker-compose.stack.yml restart hermes
```

Full teardown and recreate (preserves named volumes):
```bash
docker compose -f docker-compose.stack.yml down
docker compose -f docker-compose.stack.yml up -d --build
```

Teardown including **data loss** (removes volumes):
```bash
docker compose -f docker-compose.stack.yml down -v
```

## References

- Prior handoff: `AI_MEMORIES/HANDOFF_2026-04-24_HERMES_DELEGATED_ORCHESTRATION.md`
- Compose file: `docker-compose.stack.yml`
- Env template: `.env.stack.example`
- Repos:
  - Open WebUI: `https://github.com/galaxy-kuleon/open-webui.git` (branch `fix/hermes-e2e-selectors`)
  - Hermes Agent: `https://github.com/galaxy-kuleon/hermes-agent.git` (branch `feat/kuleon-openwebui-identity`)
  - Honcho Deploy: `https://github.com/plasticlabs/honcho-deploy.git`
