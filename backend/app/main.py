import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.core.config import settings
from app.core.database import check_database_health
from app.core.components import components_manager
from app.db.sqlite_migrator import SqliteMigrator
from app.api.v1 import api_v1_router

logger = logging.getLogger("memecoin_os")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-initialize SQLite database & migrations when in SQLite mode
    if settings.is_sqlite:
        migrator = SqliteMigrator()
        newly_applied = migrator.apply_migrations()
        if newly_applied:
            logger.info("Auto-applied SQLite migrations on startup: %s", newly_applied)
    yield

app = FastAPI(
    title=settings.APP_NAME,
    description="Multi-Chain Memecoin Intelligence, Forensics & Trading OS",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Production-safe CORS policy
allowed_origins = ["*"] if settings.APP_ENV != "production" else settings.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
@app.get("/dashboard")
async def serve_dashboard():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Dashboard UI under construction"}

@app.get("/health")
async def health_check():
    db_health = check_database_health()
    return {
        "status": "healthy" if db_health.get("status") == "healthy" else "degraded",
        "app_name": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "profile": settings.PROFILE,
        "operating_mode": settings.OPERATING_MODE,
        "database_backend": settings.DATABASE_BACKEND,
        "database_health": db_health,
        "live_trading_allowed": settings.ALLOW_LIVE_TRADING
    }

@app.get("/api/v1/system/status")
async def get_system_status():
    return {
        "app_name": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "profile": settings.PROFILE,
        "operating_mode": settings.OPERATING_MODE,
        "database_backend": settings.DATABASE_BACKEND,
        "database_health": check_database_health(),
        "live_trading_allowed": settings.ALLOW_LIVE_TRADING,
        "all_optional_disabled": components_manager.are_all_optional_disabled()
    }

@app.get("/api/v1/system/components")
async def get_system_components():
    return components_manager.get_status_report()

