#!/usr/bin/env bash
# Invocación programada técnica para el Prospector Externo en Linux y Docker.
# Control estricto de concurrencia y prevención de solapamiento mediante descriptor y flock.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

LOG_DIR="$ROOT_DIR/logs/scheduler"
LOCK_PATH="$LOG_DIR/batch.lock"

SOURCE_ID=""
DRY_RUN=false
HOLD_LOCK_SECONDS=0
EXEC_MODE="docker"
MONGO_URI=""

usage() {
  cat <<EOF
Uso: $0 --source <SOURCE_ID> [OPCIONES]

Opciones obligatorias:
  -s, --source <ID>            Identificador de la fuente a procesar (ej. bcb, bbv, finrural)

Opciones adicionales:
  -d, --dry-run               Simula la corrida sin realizar peticiones HTTP/crawling
  --hold-lock-seconds <SECS>  Retiene el bloqueo exclusivo durante N segundos (pruebas de concurrencia)
  --native                    Ejecuta el crawler usando el entorno Python local (.venv) en vez de Docker
  --mongo-uri <URI>           URI de MongoDB (por defecto: mongodb://127.0.0.1:27018 en nativo)
  -h, --help                  Muestra esta ayuda
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -s|--source)
      SOURCE_ID="$2"
      shift 2
      ;;
    -d|--dry-run)
      DRY_RUN=true
      shift
      ;;
    --hold-lock-seconds)
      HOLD_LOCK_SECONDS="$2"
      shift 2
      ;;
    --native)
      EXEC_MODE="native"
      shift
      ;;
    --mongo-uri)
      MONGO_URI="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "HU16_ERROR: Parámetro desconocido: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$SOURCE_ID" ]]; then
  echo "HU16_ERROR: Se requiere el parámetro --source <SOURCE_ID>" >&2
  usage
  exit 1
fi

if [[ ! "$SOURCE_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$ ]]; then
  echo "HU16_ERROR: Identificador de fuente inválido: '$SOURCE_ID'" >&2
  exit 1
fi

mkdir -p "$LOG_DIR"

exec 200>"$LOCK_PATH"
if ! flock -n 200; then
  echo "HU16_ALREADY_RUNNING: se omite la corrida programada; bloqueo del batch ocupado."
  exit 0
fi

cleanup() {
  local exit_code=$?
  flock -u 200 2>/dev/null || true
  exec 200>&- 2>/dev/null || true
  exit $exit_code
}
trap cleanup EXIT INT TERM

STAMP=$(date -u +"%Y%m%d_%H%M%S_%3N")
LOG_PATH="$LOG_DIR/batch_${SOURCE_ID}_${STAMP}_$$.log"

exec > >(tee -a "$LOG_PATH") 2>&1

echo "HU16_LOCK_ACQUIRED: $LOCK_PATH"
echo "HU16_SOURCE: $SOURCE_ID"
echo "HU16_LOG: $LOG_PATH"
echo "HU16_MODE: $EXEC_MODE"

cd "$ROOT_DIR"
EXIT_CODE=0

if [ "$DRY_RUN" = true ]; then
  if [ "$EXEC_MODE" = "docker" ]; then
    echo "HU16_DRY_RUN: docker compose up -d --wait --wait-timeout 120 mongo api"
    echo "HU16_DRY_RUN: docker compose run --rm --no-deps batch --source $SOURCE_ID"
  else
    echo "HU16_DRY_RUN: .venv/bin/python -m apps.crawler_batch.main --source $SOURCE_ID"
  fi
  if [ "$HOLD_LOCK_SECONDS" -gt 0 ]; then
    sleep "$HOLD_LOCK_SECONDS"
  fi
  EXIT_CODE=0
else
  if [ "$EXEC_MODE" = "docker" ]; then
    docker compose up -d --wait --wait-timeout 120 mongo api
    docker compose run --rm --no-deps batch --source "$SOURCE_ID" --force || EXIT_CODE=$?
  else
    EFFECTIVE_MONGO="${MONGO_URI:-mongodb://127.0.0.1:27018}"
    PROSPECTOR_BACKEND=mongo PROSPECTOR_MONGO_URI="$EFFECTIVE_MONGO" \
      .venv/bin/python -m apps.crawler_batch.main --source "$SOURCE_ID" --force || EXIT_CODE=$?
  fi
fi

exit "$EXIT_CODE"
