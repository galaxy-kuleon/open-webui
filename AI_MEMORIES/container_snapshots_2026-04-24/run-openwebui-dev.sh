#!/usr/bin/env bash
# Run script for the open-webui:dev-0.9.1 container (Option B, host network).
#
# - Host network so the container can reach honcho (127.0.0.1:8000), hermes
#   (127.0.0.1:8642) and ollama (host.docker.internal:11436 → host) directly.
# - PORT=8059 so we don't collide with the old GLM-OCR slot (8058) or any
#   other host service.
# - Fresh data volume (open-webui-dev-data) — no carry-over from the
#   destroyed glm-ocr_open-webui-data volume.
# - ENABLE_SIGNUP=true so first user can register; once admin is created,
#   tighten via WebUI Admin Settings.
#
# Re-run is safe: --rm removes any prior container with the same name.

set -euo pipefail

IMAGE="${OWUI_IMAGE:-open-webui:dev-0.9.1}"
NAME="${OWUI_NAME:-open-webui-dev}"
PORT="${OWUI_PORT:-8059}"
DATA_VOL="${OWUI_DATA_VOL:-open-webui-dev-data}"
SECRET_FILE="${OWUI_SECRET_FILE:-/root/open-webui/.webui_secret_key}"

if [ ! -s "$SECRET_FILE" ]; then
  echo "ERROR: $SECRET_FILE missing or empty" >&2
  exit 1
fi
SECRET="$(cat "$SECRET_FILE")"

# Source the dedicated Hermes API server key from ~/.hermes/.env if available
# so the hermes_agent pipe can auto-configure on first start.
HERMES_KEY=""
if [ -f "$HOME/.hermes/.env" ]; then
  HERMES_KEY="$(grep '^API_SERVER_KEY=' "$HOME/.hermes/.env" | cut -d '=' -f2- | tr -d '\n')"
fi

# Stop any existing container with the same name (safe — no data loss; volume persists).
docker rm -f "$NAME" 2>/dev/null || true

# Build env-var list
ENV_ARGS=(
  -e "PORT=$PORT"
  -e "WEBUI_SECRET_KEY=$SECRET"
  -e "WEBUI_AUTH=true"
  -e "ENABLE_SIGNUP=true"
  -e "DEFAULT_USER_ROLE=admin"
  -e "ENABLE_ADMIN_EXPORT=false"
  -e "ENABLE_COMMUNITY_SHARING=false"
  -e "OLLAMA_BASE_URL=http://127.0.0.1:11434"
  -e "OPENAI_API_BASE_URL=http://127.0.0.1:1234/v1"
  -e "OPENAI_API_KEY=lm-studio"
  -e "ANONYMIZED_TELEMETRY=false"
  -e "DO_NOT_TRACK=true"
  -e "SCARF_NO_ANALYTICS=true"
)

if [ -n "$HERMES_KEY" ]; then
  ENV_ARGS+=( -e "HERMES_API_KEY=$HERMES_KEY" )
fi

docker run -d \
  --name "$NAME" \
  --network host \
  --restart unless-stopped \
  "${ENV_ARGS[@]}" \
  -v "$DATA_VOL:/app/backend/data" \
  "$IMAGE"

echo "open-webui-dev started on http://127.0.0.1:$PORT  (image: $IMAGE)"
echo "  data volume: $DATA_VOL"
echo "  honcho:      http://127.0.0.1:8000   (reachable via host network)"
echo "  hermes:      http://127.0.0.1:8642   (reachable via host network)"
echo "  ollama:      http://127.0.0.1:11434  (ssh -L → agnostic-me.sh:11434)"
echo "  lmstudio:    http://127.0.0.1:1234   (ssh -L → agnostic-me.sh:1234)"
if [ -n "$HERMES_KEY" ]; then
  echo "  HERMES_API_KEY: sourced from API_SERVER_KEY in ~/.hermes/.env"
else
  echo "  HERMES_API_KEY: NOT found — hermes_agent pipe will raise ValidationError until configured"
fi
echo
echo "Configure hermes_agent pipe in WebUI (if auto-config failed):"
echo "  Workspace → Functions → hermes_agent → set valves:"
echo "    hermes_api_url=http://127.0.0.1:8642"
echo "    hermes_api_key=<API_SERVER_KEY from ~/.hermes/.env>"
