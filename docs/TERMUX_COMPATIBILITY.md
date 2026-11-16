# Android Termux Compatibility Guide

## 1. Overview

Android Termux provides a native Linux userspace environment on mobile devices. However, Termux environments differ from desktop Linux:
1. No systemd or Docker daemon.
2. Building heavy native C/C++ extensions or Rust wheels can fail or deplete RAM.
3. PostgreSQL server is not pre-installed or running by default.

---

## 2. Dependency Audit & Lightweight Profile

The lightweight profile (`requirements-lite.txt`) excludes heavy orchestrator and database dependencies:

| Dependency | Standard Profile | Lightweight Profile | Termux Status | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **FastAPI** | Yes | Yes | Compatible | Pure Python ASGI framework |
| **Uvicorn** | Yes | Yes | Compatible | Lightweight web server |
| **SQLite3** | Yes | Yes | Native Built-in | Standard Python library |
| **PyNaCl** | Yes | Yes | Pre-compiled / Clang | Used for Solana Ed25519 signatures |
| **Cryptography** | Yes | Yes | Clang / Rust wheel | Used for AES-256-GCM vault |
| **psycopg2 / asyncpg** | Yes | Excluded | Not Required | Only needed for optional PostgreSQL |
| **Docker SDK** | Yes | Excluded | Not Required | Android does not support Docker |
| **Windmill / n8n SDK** | Yes | Excluded | Not Required | Optional dual-orchestrators |

---

## 3. Installation & Run Instructions on Termux

```bash
# 1. Update packages and install python + clang
pkg update -y
pkg install -y python git clang libffi openssl

# 2. Clone repository
git clone https://github.com/harrysam11/screenerxnews.git
cd screenerxnews

# 3. Run installation script
chmod +x scripts/termux_install.sh scripts/termux_start.sh
./scripts/termux_install.sh

# 4. Start OS in lightweight mode
./scripts/termux_start.sh
```
