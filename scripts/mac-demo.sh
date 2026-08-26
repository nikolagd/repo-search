#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env.mac"
BASE_COMPOSE="${REPO_ROOT}/docker-compose.microservices.yml"
MAC_COMPOSE="${REPO_ROOT}/docker-compose.mac.yml"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing .env.mac. Create it first:"
  echo "  cp .env.mac.example .env.mac"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

compose() {
  docker compose \
    --env-file "${ENV_FILE}" \
    -f "${BASE_COMPOSE}" \
    -f "${MAC_COMPOSE}" \
    "$@"
}

preflight() {
  if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "Warning: this profile is intended for an Apple-silicon Mac."
  fi
  docker info >/dev/null
  if ! curl -fsS http://localhost:11434/api/tags >/dev/null; then
    echo "Ollama is not reachable on http://localhost:11434."
    echo "Install/open the macOS Ollama app, then run this command again."
    exit 1
  fi
}

command="${1:-up}"
case "${command}" in
  up)
    preflight
    ollama pull "${LLM_MODEL:-gemma3:4b}"
    compose up --build --detach --wait --wait-timeout 1200
    if ! compose exec --no-TTY query-service python -c \
      'import requests; requests.get("http://host.docker.internal:11434/api/tags", timeout=5).raise_for_status()'; then
      echo "Containers cannot reach native Ollama. Follow the OLLAMA_HOST step in docs/macbook-demo.md."
      exit 1
    fi
    echo "Demo is ready: http://localhost:${MICROSERVICE_FRONTEND_HOST_PORT:-8091}"
    echo "Gateway: http://localhost:${MICROSERVICE_GATEWAY_HOST_PORT:-8090}"
    ;;
  down)
    compose down
    ;;
  ps|status)
    compose ps
    ;;
  logs)
    compose logs --follow "${2:-gateway}"
    ;;
  config)
    compose config
    ;;
  *)
    echo "Usage: scripts/mac-demo.sh [up|down|status|logs [service]|config]"
    exit 2
    ;;
esac
