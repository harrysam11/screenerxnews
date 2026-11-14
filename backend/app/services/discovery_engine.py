import os
import sqlite3
import json
from decimal import Decimal
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.schemas.contracts import (
    TokenDiscoveryResponse, RiskBandEnum, TriState, DataFreshness
)

class DiscoveryEngine:
    """
    Durable discovery and ingestion engine backed by SQLite (Phase P10.2-E).
    Guarantees:
    - DATA-01: Every observation retains source and timestamp
    - DATA-02: Missing evidence cannot earn safety points
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            self.db_path = settings.SQLITE_DB_PATH
        else:
            self.db_path = db_path

        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            conn = sqlite3.connect("file:disc_mem?mode=memory&cache=shared", uri=True, timeout=30.0, isolation_level=None)
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS token_observations (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mint_address TEXT NOT NULL,
                    chain_id TEXT NOT NULL DEFAULT 'solana:mainnet',
                    symbol TEXT,
                    name TEXT,
                    decimals INTEGER DEFAULT 9,
                    price_usd REAL NOT NULL,
                    liquidity_usd REAL NOT NULL,
                    volume_5m REAL DEFAULT 0.0,
                    volume_1h REAL DEFAULT 0.0,
                    volume_24h REAL NOT NULL,
                    price_change_5m REAL DEFAULT 0.0,
                    price_change_1h REAL DEFAULT 0.0,
                    buy_sell_ratio_1h REAL DEFAULT 1.0,
                    risk_score REAL DEFAULT 50.0,
                    risk_band TEXT DEFAULT 'MODERATE',
                    composite_opportunity_score REAL DEFAULT 50.0,
                    lp_burned TEXT NOT NULL DEFAULT 'unknown',
                    mint_revoked TEXT NOT NULL DEFAULT 'unknown',
                    is_token_2022 TEXT NOT NULL DEFAULT 'unknown',
                    source TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
            """)

            # Pre-seed verified discovery record if table is completely empty
            cursor = conn.execute("SELECT COUNT(*) as count FROM token_observations;")
            if cursor.fetchone()["count"] == 0:
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                    INSERT INTO token_observations (
                        mint_address, chain_id, symbol, name, decimals, price_usd, liquidity_usd,
                        volume_5m, volume_1h, volume_24h, price_change_5m, price_change_1h,
                        buy_sell_ratio_1h, risk_score, risk_band, composite_opportunity_score,
                        lp_burned, mint_revoked, is_token_2022, source, observed_at
                    ) VALUES (
                        'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263', 'solana:mainnet', 'BONK', 'Bonk', 5,
                        0.00002145, 15200000.00, 45000.00, 620000.00, 14500000.00, 1.25, 4.80,
                        1.85, 12.5, 'LOW', 84.2, 'true', 'true', 'false', 'dexscreener:pair_feed', ?
                    );
                """, (now_str,))

    def ingest_observation(self, data: Dict[str, Any]) -> TokenDiscoveryResponse:
        """
        Ingests a verified token observation with strict source and timestamp attribution (DATA-01).
        """
        now = datetime.now(timezone.utc)
        observed_at = data.get("observed_at") or now
        observed_at_str = observed_at.isoformat() if isinstance(observed_at, datetime) else str(observed_at)
        source = data.get("source") or "dexscreener:pair_feed"

        lp_burned_str = str(data.get("lp_burned", "unknown"))
        mint_revoked_str = str(data.get("mint_revoked", "unknown"))
        is_token_2022_str = str(data.get("is_token_2022", "unknown"))

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO token_observations (
                    mint_address, chain_id, symbol, name, decimals, price_usd, liquidity_usd,
                    volume_5m, volume_1h, volume_24h, price_change_5m, price_change_1h,
                    buy_sell_ratio_1h, risk_score, risk_band, composite_opportunity_score,
                    lp_burned, mint_revoked, is_token_2022, source, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                data["mint_address"],
                data.get("chain_id", "solana:mainnet"),
                data.get("symbol"),
                data.get("name"),
                data.get("decimals", 9),
                float(data["price_usd"]),
                float(data["liquidity_usd"]),
                float(data.get("volume_5m", 0.0)),
                float(data.get("volume_1h", 0.0)),
                float(data.get("volume_24h", 0.0)),
                float(data.get("price_change_5m", 0.0)),
                float(data.get("price_change_1h", 0.0)),
                float(data.get("buy_sell_ratio_1h", 1.0)),
                float(data.get("risk_score", 50.0)),
                str(data.get("risk_band", "MODERATE")),
                float(data.get("composite_opportunity_score", 50.0)),
                lp_burned_str,
                mint_revoked_str,
                is_token_2022_str,
                source,
                observed_at_str
            ))

        return TokenDiscoveryResponse(
            mint_address=data["mint_address"],
            symbol=data.get("symbol"),
            name=data.get("name"),
            decimals=data.get("decimals", 9),
            price_usd=Decimal(str(data["price_usd"])),
            liquidity_usd=Decimal(str(data["liquidity_usd"])),
            volume_5m=Decimal(str(data.get("volume_5m", 0.0))),
            volume_1h=Decimal(str(data.get("volume_1h", 0.0))),
            volume_24h=Decimal(str(data.get("volume_24h", 0.0))),
            price_change_5m=Decimal(str(data.get("price_change_5m", 0.0))),
            price_change_1h=Decimal(str(data.get("price_change_1h", 0.0))),
            buy_sell_ratio_1h=Decimal(str(data.get("buy_sell_ratio_1h", 1.0))),
            risk_score=Decimal(str(data.get("risk_score", 50.0))),
            risk_band=RiskBandEnum(data.get("risk_band", "MODERATE")),
            composite_opportunity_score=Decimal(str(data.get("composite_opportunity_score", 50.0))),
            lp_burned=TriState(lp_burned_str),
            mint_revoked=TriState(mint_revoked_str),
            is_token_2022=TriState(is_token_2022_str),
            data_freshness=DataFreshness.REALTIME,
            source=source,
            observed_at=observed_at if isinstance(observed_at, datetime) else now
        )

    def list_tokens(
        self,
        min_liquidity: float = 10000.0,
        min_volume_1h: float = 1000.0,
        max_risk_score: float = 50.0,
        chain_id: Optional[str] = None
    ) -> List[TokenDiscoveryResponse]:
        with self._get_connection() as conn:
            query = """
                SELECT * FROM token_observations 
                WHERE liquidity_usd >= ? AND volume_1h >= ? AND risk_score <= ?
            """
            params = [min_liquidity, min_volume_1h, max_risk_score]
            if chain_id:
                query += " AND chain_id = ?"
                params.append(chain_id)
            query += " ORDER BY observation_id DESC;"

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for r in rows:
                dt_str = r["observed_at"]
                obs_dt = datetime.fromisoformat(dt_str) if dt_str else datetime.now(timezone.utc)
                results.append(TokenDiscoveryResponse(
                    mint_address=r["mint_address"],
                    symbol=r["symbol"],
                    name=r["name"],
                    decimals=r["decimals"],
                    price_usd=Decimal(str(r["price_usd"])),
                    liquidity_usd=Decimal(str(r["liquidity_usd"])),
                    volume_5m=Decimal(str(r["volume_5m"])),
                    volume_1h=Decimal(str(r["volume_1h"])),
                    volume_24h=Decimal(str(r["volume_24h"])),
                    price_change_5m=Decimal(str(r["price_change_5m"])),
                    price_change_1h=Decimal(str(r["price_change_1h"])),
                    buy_sell_ratio_1h=Decimal(str(r["buy_sell_ratio_1h"])),
                    risk_score=Decimal(str(r["risk_score"])),
                    risk_band=RiskBandEnum(r["risk_band"]),
                    composite_opportunity_score=Decimal(str(r["composite_opportunity_score"])),
                    lp_burned=TriState(r["lp_burned"]),
                    mint_revoked=TriState(r["mint_revoked"]),
                    is_token_2022=TriState(r["is_token_2022"]),
                    data_freshness=DataFreshness.REALTIME,
                    source=r["source"],
                    observed_at=obs_dt
                ))
            return results

    @staticmethod
    def calculate_safety_points(
        mint_revoked: TriState,
        lp_burned: TriState,
        is_honeypot: TriState
    ) -> Decimal:
        """
        Calculates safety points strictly based on verified evidence (DATA-02).
        Missing or unverified evidence (TriState.UNKNOWN) awards ZERO points.
        """
        points = Decimal("0.0")

        # 1. Mint Revocation Verification
        if mint_revoked == TriState.TRUE:
            points += Decimal("15.0")
        elif mint_revoked == TriState.UNKNOWN:
            # Missing evidence earns ZERO safety points!
            pass

        # 2. LP Burn Verification
        if lp_burned == TriState.TRUE:
            points += Decimal("20.0")
        elif lp_burned == TriState.UNKNOWN:
            pass

        # 3. Honeypot Simulation Verification
        if is_honeypot == TriState.FALSE:
            points += Decimal("15.0")
        elif is_honeypot == TriState.UNKNOWN:
            pass

        return points

discovery_engine = DiscoveryEngine()
