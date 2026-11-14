#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# Multi-Chain Memecoin OS - Termux Lite Profile Startup
# Starts FastAPI server with SQLite default and optional components disabled
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

if [ -d ".venv-lite" ]; then
    source .venv-lite/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

export PROFILE=lite
export DATABASE_BACKEND=sqlite
export OPERATING_MODE=${OPERATING_MODE:-paper_trading}
export ALLOW_LIVE_TRADING=false
export APP_PORT=${APP_PORT:-8000}
export HOST=${HOST:-127.0.0.1}
export ENABLE_DOCKER=false
export ENABLE_N8N=false
export ENABLE_WINDMILL=false
export ENABLE_HERMES=false
export ENABLE_REDIS=false
export PYTHONPATH=backend

echo "================================================================="
echo " Multi-Chain Memecoin OS — Lite Termux Profile"
echo " Runtime: Python + SQLite-First (Zero Docker / Zero PostgreSQL)"
echo " Operating Mode: $OPERATING_MODE | Live Trading: LOCKED (False)"
echo " Bound to: http://$HOST:$APP_PORT"
echo "================================================================="

exec uvicorn app.main:app --app-dir backend --host "$HOST" --port "$APP_PORT"
