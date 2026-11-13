from typing import List, Dict, Any, Optional
from decimal import Decimal
from datetime import datetime, timezone, timedelta
import re

class NarrativeCorrelator:
    """
    Correlates crypto news feeds, social posts, and narrative themes with assets.
    Computes a freshness-decayed, credibility-weighted narrative sentiment score (0-100).
    """
    SECTOR_KEYWORDS = {
        "AI": ["ai", "artificial intelligence", "agent", "agents", "gpu", "neural", "deepseek", "openai", "claude"],
        "POLITICS": ["trump", "biden", "election", "fed", "powell", "tariff", "white house", "congress", "senate"],
        "ANIMALS": ["dog", "cat", "pepe", "frog", "shib", "floki", "doge", "wif", "bonk", "penguin", "monkey"],
        "DEPIN": ["depin", "iot", "compute", "storage", "wireless", "sensor", "helium"],
        "GAMING": ["game", "gaming", "play", "metaverse", "nft", "rpg", "arcade"]
    }

    SOURCE_CREDIBILITY_WEIGHTS = {
        "coindesk": Decimal("1.0"),
        "cointelegraph": Decimal("0.95"),
        "theblock": Decimal("1.0"),
        "bloomberg_crypto": Decimal("1.0"),
        "reuters": Decimal("1.0"),
        "cryptoslate": Decimal("0.85"),
        "decrypt": Decimal("0.90"),
        "x_influencer": Decimal("0.60"),
        "community_rss": Decimal("0.70")
    }

    @classmethod
    def extract_sectors_from_text(cls, text: str) -> List[str]:
        matched = []
        lowered = text.lower()
        for sector, words in cls.SECTOR_KEYWORDS.items():
            for kw in words:
                pattern = r"\b" + re.escape(kw) + r"\b"
                if re.search(pattern, lowered):
                    matched.append(sector)
                    break
        return matched

    @classmethod
    def calculate_narrative_score(
        cls,
        token_symbol: str,
        token_name: str,
        token_sectors: List[str],
        news_items: List[Dict[str, Any]],
        now: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Calculates narrative momentum score (0-100) based on direct mentions,
        thematic sector correlation, source credibility, and exponential time decay.
        """
        current_time = now or datetime.now(timezone.utc)
        symbol_lower = token_symbol.lower()
        name_lower = token_name.lower()
        sectors_set = set(s.upper() for s in token_sectors)

        if not news_items:
            # Baseline neutral score with zero evidence
            return {
                "narrative_score": Decimal("50.0"),
                "direct_mentions_count": 0,
                "correlated_items_count": 0,
                "dominant_sectors": list(sectors_set),
                "average_sentiment": Decimal("0.0")
            }

        total_weight = Decimal("0.0")
        weighted_sentiment_sum = Decimal("0.0")
        direct_mentions = 0
        correlated_count = 0

        for item in news_items:
            title = item.get("title", "")
            summary = item.get("summary", "")
            content = f"{title} {summary}".lower()
            source = item.get("source_name", "").lower()
            sentiment = Decimal(str(item.get("sentiment_score", "0.0"))) # -1.0 to +1.0
            published_at = item.get("published_at")

            # Check direct mention
            is_direct = (symbol_lower in content) or (name_lower in content)
            # Check sector match
            item_sectors = set(cls.extract_sectors_from_text(content))
            has_sector_match = bool(sectors_set.intersection(item_sectors))

            if not (is_direct or has_sector_match):
                continue

            correlated_count += 1
            if is_direct:
                direct_mentions += 1

            # Time decay: half-life of 24 hours
            if published_at:
                if isinstance(published_at, str):
                    try:
                        pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                    except Exception:
                        pub_dt = current_time
                else:
                    pub_dt = published_at
                age_hours = max(Decimal("0.0"), Decimal(str((current_time - pub_dt).total_seconds() / 3600.0)))
            else:
                age_hours = Decimal("0.0")

            # Decay factor = 0.5 ^ (age_hours / 24)
            decay = Decimal(str(0.5 ** (float(age_hours) / 24.0)))

            # Source weight
            credibility = cls.SOURCE_CREDIBILITY_WEIGHTS.get(source, Decimal("0.75"))

            # Multiplier: direct mentions get 2.5x weight
            relevance_multiplier = Decimal("2.5") if is_direct else Decimal("1.0")

            item_weight = credibility * relevance_multiplier
            total_weight += item_weight
            weighted_sentiment_sum += (sentiment * decay * item_weight)

        if total_weight > Decimal("0.0"):
            avg_sentiment = weighted_sentiment_sum / total_weight
            # Convert sentiment [-1.0, +1.0] to base score [0, 100] (0 = -1.0, 50 = 0.0, 100 = +1.0)
            base_score = Decimal("50.0") + (avg_sentiment * Decimal("40.0"))
            
            # Volume boost based on direct mentions
            volume_boost = min(Decimal("10.0"), Decimal(str(direct_mentions * 2)))
            final_score = max(Decimal("0.0"), min(Decimal("100.0"), base_score + volume_boost))
        else:
            avg_sentiment = Decimal("0.0")
            final_score = Decimal("50.0")

        return {
            "narrative_score": round(final_score, 1),
            "direct_mentions_count": direct_mentions,
            "correlated_items_count": correlated_count,
            "dominant_sectors": list(sectors_set),
            "average_sentiment": round(avg_sentiment, 2)
        }

narrative_correlator = NarrativeCorrelator()
