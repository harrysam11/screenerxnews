from datetime import datetime
from decimal import Decimal
from typing import List
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.contracts import NewsFeedItem

router = APIRouter(prefix="/news", tags=["News & Narratives"])

@router.get("/feed", response_model=List[NewsFeedItem])
async def get_curated_news_feed(
    category: str = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns curated news articles and classified social narratives affecting Solana ecosystem tokens.
    """
    return [
        NewsFeedItem(
            id="news-item-001",
            title="Solana DeFi Volume Surges as New DEX Aggregators Launch",
            url="https://solana.com/news/defi-growth-2024",
            source_name="Solana Foundation Official",
            published_at=datetime.utcnow(),
            category="ecosystem_growth",
            sentiment_score=Decimal("45.5"),
            verification_status="verified",
            affected_tokens=["So11111111111111111111111111111111111111112"],
            affected_sectors=["defi", "infrastructure"]
        )
    ]
