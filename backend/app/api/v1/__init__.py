from fastapi import APIRouter
from app.api.v1.discovery import router as discovery_router
from app.api.v1.forensics import router as forensics_router
from app.api.v1.news import router as news_router
from app.api.v1.scoring import router as scoring_router
from app.api.v1.paper import router as paper_router
from app.api.v1.execution import router as execution_router
from app.api.v1.portfolio import router as portfolio_router
from app.api.v1.hermes import router as hermes_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.providers import router as providers_router
from app.api.v1.strategies import router as strategies_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(discovery_router)
api_v1_router.include_router(forensics_router)
api_v1_router.include_router(news_router)
api_v1_router.include_router(scoring_router)
api_v1_router.include_router(paper_router)
api_v1_router.include_router(execution_router)
api_v1_router.include_router(portfolio_router)
api_v1_router.include_router(hermes_router)
api_v1_router.include_router(dashboard_router)
api_v1_router.include_router(providers_router)
api_v1_router.include_router(strategies_router)


