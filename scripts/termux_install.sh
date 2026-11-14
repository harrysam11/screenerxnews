#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# Multi-Chain Memecoin OS - Termux Lite Profile Setup
# Installs Python and minimal dependencies without Docker or PostgreSQL
# ==============================================================================

set -e

echo "=== [1/4] Updating Termux packages ==="
pkg update -y
pkg install -y python clang libffi openssl git

echo "=== [2/4] Setting up Python virtual environment ==="
if [ ! -d ".venv-lite" ]; then
    python -m venv .venv-lite
fi
source .venv-lite/bin/activate

echo "=== [3/4] Installing Lite / Termux requirements ==="
pip install --upgrade pip setuptools wheel
pip install -r requirements-lite.txt

echo "=== [4/4] Initializing SQLite-First Database ==="
export PROFILE=lite
export DATABASE_BACKEND=sqlite
export PYTHONPATH=backend
python -c "
from app.db.sqlite_migrator import SqliteMigrator
migrator = SqliteMigrator()
applied = migrator.apply_migrations()
print('Database initialized. Applied migrations:', applied)
"

echo "=== Setup complete! Start with: ./scripts/termux_start.sh ==="
