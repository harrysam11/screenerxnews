import os
import sqlite3
import json
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("memecoin_os.research_history")

# Default configuration paths
DEFAULT_RESEARCH_DB_PATH = "data/research_history.db"
DEFAULT_OPERATIONAL_DB_PATH = "data/memecoin_os.db"


def format_decimal_exact(val: Any) -> Optional[str]:
    """
    Serializes a Decimal, int, float or numeric string to an exact fixed-point string
    without scientific notation, trailing decimal points, or precision clipping.
    Returns None if value is None or empty.
    """
    if val is None or val == "":
        return None
    if isinstance(val, Decimal):
        d = val
    elif isinstance(val, int):
        d = Decimal(val)
    elif isinstance(val, float):
        d = Decimal(str(val))
    elif isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return None
        try:
            d = Decimal(val_str)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid decimal literal: {val!r}") from exc
    else:
        raise TypeError(f"Expected Decimal, int, float, or str, got {type(val).__name__} ({val!r})")

    if not d.is_finite():
        raise ValueError(f"Cannot serialize non-finite financial decimal: {val!r}")

    sign, digits, exponent = d.as_tuple()
    if not digits or all(digit == 0 for digit in digits):
        return "0"

    digits_str = "".join(str(digit) for digit in digits)
    if exponent >= 0:
        res = digits_str + ("0" * exponent)
    else:
        k = -exponent
        n = len(digits_str)
        if n > k:
            integer_part = digits_str[:n - k]
            fractional_part = digits_str[n - k:].rstrip("0")
        else:
            integer_part = "0"
            fractional_part = ("0" * (k - n) + digits_str).rstrip("0")

        if fractional_part:
            res = f"{integer_part}.{fractional_part}"
        else:
            res = integer_part

    if sign == 1 and res != "0":
        return f"-{res}"
    return res


def normalize_utc_timestamp(val: Any) -> str:
    """
    Normalizes any datetime or ISO timestamp string into UTC ISO-8601 with trailing 'Z'.
    """
    if isinstance(val, datetime):
        dt = val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(val, str):
        val_clean = val.strip()
        if not val_clean:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            # Handle ISO formats
            dt = datetime.fromisoformat(val_clean.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return val_clean
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ResearchHistoryStore:
    """
    Dedicated, isolated SQLite persistence engine for Phase P1.4 Research Intelligence.
    Guarantees:
    - Zero connection to the operational database (fail-closed guard against data/memecoin_os.db and symlinks).
    - Immutable observation append log with idempotency keys.
    - Financial decimals stored as exact TEXT.
    - Bounded retention pruning with configurable sliding window and per-token observation caps.
    - WAL mode with busy_timeout=30000 and serialized write transactions.
    - Completely independent of SqliteMigrator and FastAPI lifespan.
    """

    def __init__(self, db_path: Optional[str] = None):
        target_path = db_path or os.environ.get("RESEARCH_HISTORY_DB_PATH", DEFAULT_RESEARCH_DB_PATH)
        self.db_path = self._validate_and_canonicalize_path(target_path)
        self._mem_conn = None
        self._ensure_parent_directory()
        self._init_schema()

    @staticmethod
    def _validate_and_canonicalize_path(path: str) -> str:
        """
        FAIL-CLOSED SAFETY GUARD:
        Validates the research database path. Strictly rejects the operational database
        path 'data/memecoin_os.db', its absolute path, or any symlink pointing to it.
        """
        if not path or not path.strip():
            raise ValueError("RESEARCH_HISTORY_DB_PATH cannot be empty.")

        # Special test in-memory bypass
        if path == ":memory:":
            return ":memory:"

        resolved_target = os.path.realpath(os.path.abspath(path))
        operational_target = os.path.realpath(os.path.abspath(DEFAULT_OPERATIONAL_DB_PATH))

        if (
            resolved_target == operational_target
            or resolved_target.endswith("/data/memecoin_os.db")
            or os.path.basename(resolved_target) == "memecoin_os.db"
        ):
            raise RuntimeError(
                f"SAFETY VIOLATION (FAIL-CLOSED): Research history store attempted to use operational database path '{resolved_target}'. "
                f"Research history MUST be stored in a dedicated database (default: data/research_history.db)."
            )

        return resolved_target

    def _ensure_parent_directory(self) -> None:
        if self.db_path != ":memory:":
            dir_path = os.path.dirname(self.db_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", timeout=30.0, check_same_thread=False)
                self._mem_conn.row_factory = sqlite3.Row
                self._mem_conn.execute("PRAGMA foreign_keys = ON;")
            return self._mem_conn

        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def _init_schema(self) -> None:
        """Initializes tables for P1.4 research history independently."""
        with self._get_connection() as conn:
            # Upgrade legacy token_momentum_metrics schema if already present on disk
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='token_momentum_metrics';")
            if cur.fetchone():
                existing = [r[1] for r in conn.execute("PRAGMA table_info(token_momentum_metrics);").fetchall()]
                for col_name, col_type in [
                    ("historical_velocity_5m_pct", "REAL"),
                    ("historical_velocity_1h_pct", "REAL"),
                    ("historical_velocity_24h_pct", "REAL"),
                    ("provider_price_change_5m_pct", "REAL"),
                    ("provider_price_change_1h_pct", "REAL"),
                    ("provider_price_change_24h_pct", "REAL"),
                    ("historical_calculation_status", "TEXT NOT NULL DEFAULT 'HISTORICAL_DATA_UNAVAILABLE'"),
                    ("metric_type", "TEXT NOT NULL DEFAULT 'LATEST_STATE_PROJECTION'")
                ]:
                    if col_name not in existing:
                        conn.execute(f"ALTER TABLE token_momentum_metrics ADD COLUMN {col_name} {col_type};")

            conn.executescript("""
                CREATE TABLE IF NOT EXISTS historical_token_observations (
                    id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    symbol TEXT,
                    name TEXT,
                    price_usd TEXT,
                    price_native TEXT,
                    liquidity_usd TEXT,
                    volume_24h_usd TEXT,
                    volume_1h_usd TEXT,
                    volume_5m_usd TEXT,
                    price_change_5m_pct REAL,
                    price_change_1h_pct REAL,
                    price_change_24h_pct REAL,
                    fdv_usd TEXT,
                    market_cap_usd TEXT,
                    txns_24h_total INTEGER,
                    quality_score REAL,
                    scoring_version TEXT NOT NULL DEFAULT 'v1.4_research_vector',
                    anomaly_flags_json TEXT NOT NULL DEFAULT '[]',
                    canonical_candidate_flag INTEGER NOT NULL DEFAULT 0,
                    provider_source TEXT NOT NULL,
                    endpoint_sources_json TEXT NOT NULL DEFAULT '[]',
                    observed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_hist_tok_chain_addr_obs 
                    ON historical_token_observations(chain_id, token_address, observed_at);
                CREATE INDEX IF NOT EXISTS idx_hist_tok_observed_at 
                    ON historical_token_observations(observed_at);
                CREATE INDEX IF NOT EXISTS idx_hist_tok_symbol 
                    ON historical_token_observations(symbol);

                CREATE TABLE IF NOT EXISTS token_momentum_metrics (
                    metric_id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    symbol TEXT,
                    metric_type TEXT NOT NULL DEFAULT 'LATEST_STATE_PROJECTION',
                    provider_price_change_5m_pct REAL,
                    provider_price_change_1h_pct REAL,
                    provider_price_change_24h_pct REAL,
                    historical_velocity_5m_pct REAL,
                    historical_velocity_1h_pct REAL,
                    historical_velocity_24h_pct REAL,
                    historical_calculation_status TEXT NOT NULL DEFAULT 'HISTORICAL_DATA_UNAVAILABLE',
                    volume_acceleration_ratio REAL,
                    liquidity_migration_direction TEXT NOT NULL CHECK(liquidity_migration_direction IN ('EXPANDING', 'STABLE', 'CONTRACTING', 'INSUFFICIENT_DATA')),
                    liquidity_delta_usd TEXT,
                    observations_count INTEGER NOT NULL DEFAULT 1,
                    first_observed_at TEXT NOT NULL,
                    last_observed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(chain_id, token_address)
                );

                CREATE INDEX IF NOT EXISTS idx_momentum_chain_addr 
                    ON token_momentum_metrics(chain_id, token_address);
                CREATE INDEX IF NOT EXISTS idx_momentum_hist_vel_1h 
                    ON token_momentum_metrics(historical_velocity_1h_pct);
                CREATE INDEX IF NOT EXISTS idx_momentum_prov_vel_1h 
                    ON token_momentum_metrics(provider_price_change_1h_pct);

                CREATE TABLE IF NOT EXISTS research_watchlists (
                    watchlist_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    criteria_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_feature_vectors (
                    feature_id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    token_address TEXT NOT NULL,
                    symbol TEXT,
                    feature_version TEXT NOT NULL DEFAULT 'v1.4_research_vector',
                    observation_timestamp TEXT NOT NULL,
                    source_provenance TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    UNIQUE(chain_id, token_address, feature_version)
                );

                CREATE TABLE IF NOT EXISTS research_pruning_audit_log (
                    prune_id TEXT PRIMARY KEY,
                    retention_days INTEGER NOT NULL,
                    max_per_token INTEGER NOT NULL,
                    rows_pruned INTEGER NOT NULL,
                    pruned_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS historical_ohlcv_candles (
                    id TEXT PRIMARY KEY,
                    chain_id TEXT NOT NULL,
                    pool_address TEXT NOT NULL,
                    base_token_address TEXT,
                    quote_token_address TEXT,
                    timeframe TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    timestamp_unix INTEGER NOT NULL,
                    end_timestamp_utc TEXT NOT NULL,
                    open_price TEXT NOT NULL,
                    high_price TEXT NOT NULL,
                    low_price TEXT NOT NULL,
                    close_price TEXT NOT NULL,
                    volume TEXT NOT NULL,
                    price_currency TEXT NOT NULL DEFAULT 'USD',
                    volume_currency TEXT NOT NULL DEFAULT 'USD',
                    bar_status TEXT NOT NULL DEFAULT 'CLOSED',
                    provider TEXT NOT NULL DEFAULT 'geckoterminal',
                    provider_version TEXT NOT NULL DEFAULT 'v2_20230302',
                    revision_count INTEGER NOT NULL DEFAULT 0,
                    ingested_at TEXT NOT NULL,
                    UNIQUE(chain_id, pool_address, timeframe, timestamp_utc, price_currency, volume_currency)
                );

                CREATE INDEX IF NOT EXISTS idx_ohlcv_pool_tf_time 
                    ON historical_ohlcv_candles(chain_id, pool_address, timeframe, timestamp_unix);
                CREATE INDEX IF NOT EXISTS idx_ohlcv_time 
                    ON historical_ohlcv_candles(timestamp_unix);

                CREATE TABLE IF NOT EXISTS candle_revision_audit_log (
                    audit_id TEXT PRIMARY KEY,
                    candle_id TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    pool_address TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    old_ohlcv_json TEXT NOT NULL,
                    new_ohlcv_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS declarative_strategies (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    current_version TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ENABLED',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS declarative_strategy_versions (
                    version_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    change_summary TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (strategy_id) REFERENCES declarative_strategies(id)
                );

                CREATE INDEX IF NOT EXISTS idx_strat_versions_id 
                    ON declarative_strategy_versions(strategy_id);

                CREATE TABLE IF NOT EXISTS strategy_paper_accounts (
                    account_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    cash_balance_usd TEXT NOT NULL,
                    initial_balance_usd TEXT NOT NULL,
                    realized_pnl_usd TEXT NOT NULL DEFAULT '0',
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategy_paper_orders (
                    order_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    pool_address TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    price TEXT NOT NULL,
                    fee_usd TEXT NOT NULL,
                    slippage_usd TEXT NOT NULL,
                    status TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    filled_at TEXT,
                    FOREIGN KEY (account_id) REFERENCES strategy_paper_accounts(account_id)
                );

                CREATE TABLE IF NOT EXISTS strategy_paper_positions (
                    position_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    chain_id TEXT NOT NULL,
                    pool_address TEXT NOT NULL,
                    side TEXT NOT NULL DEFAULT 'BUY',
                    quantity TEXT NOT NULL,
                    entry_price TEXT NOT NULL,
                    cost_basis_usd TEXT NOT NULL,
                    realized_pnl_usd TEXT NOT NULL DEFAULT '0',
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    FOREIGN KEY (account_id) REFERENCES strategy_paper_accounts(account_id)
                );

                CREATE INDEX IF NOT EXISTS idx_paper_orders_acc ON strategy_paper_orders(account_id);
                CREATE INDEX IF NOT EXISTS idx_paper_positions_acc ON strategy_paper_positions(account_id);
            """)
            conn.commit()

    # -------------------------------------------------------------------------
    # Observation Log Ingestion & Query
    # -------------------------------------------------------------------------
    def ingest_token_observation(self, obs: Dict[str, Any]) -> bool:
        """
        Inserts a single immutable market observation.
        Uses idempotency key: (chain_id, token_address.lower(), observed_at).
        Returns True if inserted, False if duplicate skipped.
        """
        chain_id = obs["chain_id"]
        token_address = obs["token_address"].lower()
        observed_at = normalize_utc_timestamp(obs.get("observed_at") or obs.get("fetched_at"))
        idempotency_key = f"{chain_id}:{token_address}:{observed_at}"

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        obs_id = obs.get("id") or f"obs_{int(datetime.now(timezone.utc).timestamp()*1000)}_{os.urandom(4).hex()}"

        with self._get_connection() as conn:
            try:
                conn.execute("""
                    INSERT INTO historical_token_observations (
                        id, chain_id, token_address, symbol, name,
                        price_usd, price_native, liquidity_usd, volume_24h_usd,
                        volume_1h_usd, volume_5m_usd, price_change_5m_pct,
                        price_change_1h_pct, price_change_24h_pct, fdv_usd,
                        market_cap_usd, txns_24h_total, quality_score,
                        scoring_version, anomaly_flags_json, canonical_candidate_flag,
                        provider_source, endpoint_sources_json, observed_at,
                        created_at, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    obs_id,
                    chain_id,
                    token_address,
                    obs.get("symbol"),
                    obs.get("name"),
                    format_decimal_exact(obs.get("price_usd")),
                    format_decimal_exact(obs.get("price_native")),
                    format_decimal_exact(obs.get("liquidity_usd")),
                    format_decimal_exact(obs.get("volume_24h_usd")),
                    format_decimal_exact(obs.get("volume_1h_usd")),
                    format_decimal_exact(obs.get("volume_5m_usd")),
                    float(obs["price_change_5m_pct"]) if obs.get("price_change_5m_pct") is not None else None,
                    float(obs["price_change_1h_pct"]) if obs.get("price_change_1h_pct") is not None else None,
                    float(obs["price_change_24h_pct"]) if obs.get("price_change_24h_pct") is not None else None,
                    format_decimal_exact(obs.get("fdv_usd")),
                    format_decimal_exact(obs.get("market_cap_usd")),
                    int(obs["txns_24h_total"]) if obs.get("txns_24h_total") is not None else None,
                    float(obs["quality_score"]) if obs.get("quality_score") is not None else None,
                    obs.get("scoring_version") or "v1.4_research_vector",
                    json.dumps(obs.get("anomaly_flags") or []),
                    1 if obs.get("canonical_candidate_flag") else 0,
                    obs.get("provider_source") or "unknown",
                    json.dumps(obs.get("endpoint_sources") or []),
                    observed_at,
                    now_utc,
                    idempotency_key
                ))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                # Duplicate idempotency key, safely skip
                return False

    def ingest_token_observations_batch(self, obs_list: List[Dict[str, Any]]) -> int:
        """Batch-ingests token observations. Returns count of newly inserted rows."""
        inserted_count = 0
        for obs in obs_list:
            if self.ingest_token_observation(obs):
                inserted_count += 1
        return inserted_count

    def query_token_observations(
        self,
        chain_id: str,
        token_address: str,
        limit: int = 100,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Queries historical observations for a token ordered chronologically descending."""
        query = "SELECT * FROM historical_token_observations WHERE chain_id = ? AND token_address = ?"
        params: List[Any] = [chain_id, token_address.lower()]

        if start_time:
            query += " AND observed_at >= ?"
            params.append(normalize_utc_timestamp(start_time))
        if end_time:
            query += " AND observed_at <= ?"
            params.append(normalize_utc_timestamp(end_time))

        query += " ORDER BY observed_at DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    # -------------------------------------------------------------------------
    # Momentum Metrics Persistence
    # -------------------------------------------------------------------------
    def upsert_momentum_metrics(self, metrics: Dict[str, Any]) -> None:
        """Upserts derived token momentum & liquidity migration metrics."""
        chain_id = metrics["chain_id"]
        token_address = metrics["token_address"].lower()
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        metric_id = metrics.get("metric_id") or f"mom_{chain_id}_{token_address}"
        metric_type = metrics.get("metric_type") or "LATEST_STATE_PROJECTION"

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO token_momentum_metrics (
                    metric_id, chain_id, token_address, symbol, metric_type,
                    provider_price_change_5m_pct, provider_price_change_1h_pct, provider_price_change_24h_pct,
                    historical_velocity_5m_pct, historical_velocity_1h_pct, historical_velocity_24h_pct,
                    historical_calculation_status, volume_acceleration_ratio,
                    liquidity_migration_direction, liquidity_delta_usd, observations_count,
                    first_observed_at, last_observed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain_id, token_address) DO UPDATE SET
                    symbol = excluded.symbol,
                    metric_type = excluded.metric_type,
                    provider_price_change_5m_pct = excluded.provider_price_change_5m_pct,
                    provider_price_change_1h_pct = excluded.provider_price_change_1h_pct,
                    provider_price_change_24h_pct = excluded.provider_price_change_24h_pct,
                    historical_velocity_5m_pct = excluded.historical_velocity_5m_pct,
                    historical_velocity_1h_pct = excluded.historical_velocity_1h_pct,
                    historical_velocity_24h_pct = excluded.historical_velocity_24h_pct,
                    historical_calculation_status = excluded.historical_calculation_status,
                    volume_acceleration_ratio = excluded.volume_acceleration_ratio,
                    liquidity_migration_direction = excluded.liquidity_migration_direction,
                    liquidity_delta_usd = excluded.liquidity_delta_usd,
                    observations_count = excluded.observations_count,
                    first_observed_at = excluded.first_observed_at,
                    last_observed_at = excluded.last_observed_at,
                    updated_at = excluded.updated_at;
            """, (
                metric_id,
                chain_id,
                token_address,
                metrics.get("symbol"),
                metric_type,
                metrics.get("provider_price_change_5m_pct"),
                metrics.get("provider_price_change_1h_pct"),
                metrics.get("provider_price_change_24h_pct"),
                metrics.get("historical_velocity_5m_pct"),
                metrics.get("historical_velocity_1h_pct"),
                metrics.get("historical_velocity_24h_pct"),
                metrics.get("historical_calculation_status", "HISTORICAL_DATA_UNAVAILABLE"),
                metrics.get("volume_acceleration_ratio"),
                metrics.get("liquidity_migration_direction", "INSUFFICIENT_DATA"),
                format_decimal_exact(metrics.get("liquidity_delta_usd")),
                int(metrics.get("observations_count", 1)),
                normalize_utc_timestamp(metrics["first_observed_at"]),
                normalize_utc_timestamp(metrics["last_observed_at"]),
                now_utc
            ))
            conn.commit()

    def get_momentum_metrics(self, chain_id: str, token_address: str) -> Optional[Dict[str, Any]]:
        """Retrieves current momentum metrics for a specific token."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM token_momentum_metrics WHERE chain_id = ? AND token_address = ?;",
                (chain_id, token_address.lower())
            ).fetchone()
            return dict(row) if row else None

    def query_momentum_leaders(
        self,
        chain_id: Optional[str] = None,
        sort_by: str = "historical_velocity_1h_pct",
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Returns momentum leaderboards ordered by chosen velocity metric."""
        valid_sorts = {
            "historical_velocity_5m_pct": "historical_velocity_5m_pct",
            "historical_velocity_1h_pct": "historical_velocity_1h_pct",
            "historical_velocity_24h_pct": "historical_velocity_24h_pct",
            "velocity_1h_pct": "historical_velocity_1h_pct",
            "velocity_5m_pct": "historical_velocity_5m_pct",
            "velocity_24h_pct": "historical_velocity_24h_pct",
            "provider_price_change_5m_pct": "provider_price_change_5m_pct",
            "provider_price_change_1h_pct": "provider_price_change_1h_pct",
            "provider_price_change_24h_pct": "provider_price_change_24h_pct",
            "volume_acceleration_ratio": "volume_acceleration_ratio"
        }
        sort_column = valid_sorts.get(sort_by, "historical_velocity_1h_pct")

        query = f"SELECT * FROM token_momentum_metrics"
        params: List[Any] = []
        if chain_id and chain_id.lower() != "all":
            query += " WHERE chain_id = ?"
            params.append(chain_id)

        query += f" ORDER BY {sort_column} DESC NULLS LAST LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    # -------------------------------------------------------------------------
    # Research Watchlists (Explicit CRUD)
    # -------------------------------------------------------------------------
    def create_watchlist(self, name: str, description: Optional[str] = None, criteria: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Creates a new user-defined research watchlist."""
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        watchlist_id = f"wl_{int(datetime.now(timezone.utc).timestamp())}_{os.urandom(3).hex()}"
        criteria_json = json.dumps(criteria or {})

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO research_watchlists (watchlist_id, name, description, criteria_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?);
            """, (watchlist_id, name, description or "", criteria_json, now_utc, now_utc))
            conn.commit()

        return {
            "watchlist_id": watchlist_id,
            "name": name,
            "description": description or "",
            "criteria": criteria or {},
            "created_at": now_utc,
            "updated_at": now_utc
        }

    def get_watchlists(self) -> List[Dict[str, Any]]:
        """Lists all persistent research watchlists."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM research_watchlists ORDER BY created_at DESC;").fetchall()
            results = []
            for r in rows:
                item = dict(r)
                try:
                    item["criteria"] = json.loads(item["criteria_json"])
                except Exception:
                    item["criteria"] = {}
                results.append(item)
            return results

    def get_watchlist(self, watchlist_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single research watchlist by ID."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM research_watchlists WHERE watchlist_id = ?;", (watchlist_id,)).fetchone()
            if not row:
                return None
            item = dict(row)
            try:
                item["criteria"] = json.loads(item["criteria_json"])
            except Exception:
                item["criteria"] = {}
            return item

    def update_watchlist(
        self,
        watchlist_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        criteria: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Updates an existing research watchlist."""
        existing = self.get_watchlist(watchlist_id)
        if not existing:
            return None

        new_name = name if name is not None else existing["name"]
        new_desc = description if description is not None else existing["description"]
        new_crit = criteria if criteria is not None else existing["criteria"]
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        with self._get_connection() as conn:
            conn.execute("""
                UPDATE research_watchlists
                SET name = ?, description = ?, criteria_json = ?, updated_at = ?
                WHERE watchlist_id = ?;
            """, (new_name, new_desc, json.dumps(new_crit), now_utc, watchlist_id))
            conn.commit()

        return {
            "watchlist_id": watchlist_id,
            "name": new_name,
            "description": new_desc,
            "criteria": new_crit,
            "created_at": existing["created_at"],
            "updated_at": now_utc
        }

    def delete_watchlist(self, watchlist_id: str) -> bool:
        """Deletes a research watchlist by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM research_watchlists WHERE watchlist_id = ?;", (watchlist_id,))
            conn.commit()
            return cursor.rowcount > 0

    # -------------------------------------------------------------------------
    # Strategy Feature Vectors
    # -------------------------------------------------------------------------
    def store_strategy_feature_vector(self, feature: Dict[str, Any]) -> Dict[str, Any]:
        """Stores a versioned, reproducible strategy feature vector."""
        chain_id = feature["chain_id"]
        token_address = feature["token_address"].lower()
        symbol = feature.get("symbol")
        feature_version = feature.get("feature_version") or "v1.4_research_vector"
        observation_timestamp = normalize_utc_timestamp(feature.get("observation_timestamp"))
        source_provenance = feature.get("source_provenance") or "unknown"
        vector_data = feature.get("vector") or {}
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        feature_id = f"feat_{chain_id}_{token_address}_{feature_version}"

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO strategy_feature_vectors (
                    feature_id, chain_id, token_address, symbol, feature_version,
                    observation_timestamp, source_provenance, vector_json, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain_id, token_address, feature_version) DO UPDATE SET
                    symbol = excluded.symbol,
                    observation_timestamp = excluded.observation_timestamp,
                    source_provenance = excluded.source_provenance,
                    vector_json = excluded.vector_json,
                    generated_at = excluded.generated_at;
            """, (feature_id, chain_id, token_address, symbol, feature_version, observation_timestamp, source_provenance, json.dumps(vector_data, default=str), now_utc))
            conn.commit()

        return {
            "feature_id": feature_id,
            "chain_id": chain_id,
            "token_address": token_address,
            "symbol": symbol,
            "feature_version": feature_version,
            "observation_timestamp": observation_timestamp,
            "source_provenance": source_provenance,
            "vector": vector_data,
            "generated_at": now_utc
        }

    def get_strategy_feature_vector(
        self,
        chain_id: str,
        token_address: str,
        feature_version: str = "v1.4_research_vector"
    ) -> Optional[Dict[str, Any]]:
        """Retrieves a stored strategy feature vector."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_feature_vectors WHERE chain_id = ? AND token_address = ? AND feature_version = ?;",
                (chain_id, token_address.lower(), feature_version)
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            try:
                item["vector"] = json.loads(item["vector_json"])
            except Exception:
                item["vector"] = {}
            return item

    # -------------------------------------------------------------------------
    # Bounded Retention Pruning
    # -------------------------------------------------------------------------
    def prune_aged_observations(
        self,
        retention_days: int = 14,
        max_observations_per_token: int = 500,
        batch_size: int = 1000
    ) -> Dict[str, Any]:
        """
        Executes bounded deletion of aged observations:
        1. Deletes observations older than cutoff date (default 14 days) in bounded batches.
        2. Prunes observations exceeding max_observations_per_token per asset.
        3. Never touches the operational database.
        Returns pruning audit metrics.
        """
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cutoff_iso = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        pruned_count = 0

        with self._get_connection() as conn:
            # 1. Day-based pruning in bounded batches
            while True:
                cursor = conn.execute("""
                    DELETE FROM historical_token_observations
                    WHERE id IN (
                        SELECT id FROM historical_token_observations
                        WHERE observed_at < ?
                        LIMIT ?
                    );
                """, (cutoff_iso, batch_size))
                conn.commit()
                deleted = cursor.rowcount
                pruned_count += deleted
                if deleted < batch_size:
                    break

            # 2. Per-token cap pruning (keep newest max_observations_per_token)
            tokens_with_excess = conn.execute("""
                SELECT chain_id, token_address, COUNT(*) as cnt
                FROM historical_token_observations
                GROUP BY chain_id, token_address
                HAVING cnt > ?;
            """, (max_observations_per_token,)).fetchall()

            for row in tokens_with_excess:
                c_id = row["chain_id"]
                t_addr = row["token_address"]
                excess_count = row["cnt"] - max_observations_per_token
                if excess_count > 0:
                    cursor = conn.execute("""
                        DELETE FROM historical_token_observations
                        WHERE id IN (
                            SELECT id FROM historical_token_observations
                            WHERE chain_id = ? AND token_address = ?
                            ORDER BY observed_at ASC
                            LIMIT ?
                        );
                    """, (c_id, t_addr, excess_count))
                    conn.commit()
                    pruned_count += cursor.rowcount

            # 3. Log audit event
            prune_id = f"prune_{int(datetime.now(timezone.utc).timestamp())}_{os.urandom(3).hex()}"
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute("""
                INSERT INTO research_pruning_audit_log (prune_id, retention_days, max_per_token, rows_pruned, pruned_at)
                VALUES (?, ?, ?, ?, ?);
            """, (prune_id, retention_days, max_observations_per_token, pruned_count, now_utc))
            conn.commit()

        logger.info("Research history store pruned %d observations (retention=%d days, cap=%d)", pruned_count, retention_days, max_observations_per_token)
        return {
            "prune_id": prune_id,
            "retention_days": retention_days,
            "max_per_token": max_observations_per_token,
            "rows_pruned": pruned_count,
            "cutoff_timestamp": cutoff_iso,
            "pruned_at": now_utc
        }

    # -------------------------------------------------------------------------
    # OHLCV Candlestick Storage & Conflicting Revision Policy
    # -------------------------------------------------------------------------
    def upsert_candle(self, c: Dict[str, Any]) -> str:
        """
        Idempotently inserts or updates an OHLCV candle.
        Distinguishes:
        - NEW_INSERT: brand new candle.
        - IDENTICAL_DUPLICATE: same timestamp and identical OHLCV values (no-op).
        - CONFLICTING_UPDATE: same timestamp but changed OHLCV values; records revision audit log,
          increments revision_count, and updates candle.
        - REJECTED_BOUNDS: mathematical violation (e.g. high < low or low < 0).
        """
        chain_id = c["chain_id"]
        pool_address = c["pool_address"].lower()
        timeframe = c["timeframe"]
        price_curr = c.get("price_currency", "USD")
        vol_curr = c.get("volume_currency", "USD")

        # Parse numeric values
        try:
            op = format_decimal_exact(c["open_price"])
            hp = format_decimal_exact(c["high_price"])
            lp = format_decimal_exact(c["low_price"])
            cp = format_decimal_exact(c["close_price"])
            vol = format_decimal_exact(c["volume"])
            d_op, d_hp, d_lp, d_cp, d_vol = Decimal(op), Decimal(hp), Decimal(lp), Decimal(cp), Decimal(vol)
        except Exception as e:
            logger.warning("Numeric parse error for candle: %s", e)
            return "REJECTED_BOUNDS"

        # Bounds validation
        if d_lp < Decimal("0") or d_op < Decimal("0") or d_cp < Decimal("0") or d_vol < Decimal("0"):
            return "REJECTED_BOUNDS"
        if d_hp < d_op or d_hp < d_cp or d_lp > d_op or d_lp > d_cp or d_hp < d_lp:
            return "REJECTED_BOUNDS"

        ts_utc = normalize_utc_timestamp(c.get("timestamp_utc") or c.get("timestamp"))
        ts_unix = int(c.get("timestamp_unix") or datetime.fromisoformat(ts_utc.replace("Z", "+00:00")).timestamp())
        end_utc = c.get("end_timestamp_utc")
        if not end_utc:
            # Fallback end timestamp computation
            dt_start = datetime.fromtimestamp(ts_unix, tz=timezone.utc)
            from app.schemas.ohlcv import Timeframe
            try:
                tf_obj = Timeframe(timeframe)
                dt_end = dt_start + timedelta(seconds=tf_obj.seconds)
            except Exception:
                dt_end = dt_start + timedelta(seconds=300)
            end_utc = dt_end.strftime("%Y-%m-%dT%H:%M:%SZ")

        bar_status = c.get("bar_status", "CLOSED")
        provider = c.get("provider", "geckoterminal")
        provider_ver = c.get("provider_version", "v2_20230302")
        base_addr = (c.get("base_token_address") or "").lower() or None
        quote_addr = (c.get("quote_token_address") or "").lower() or None
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        with self._get_connection() as conn:
            existing = conn.execute("""
                SELECT id, open_price, high_price, low_price, close_price, volume, revision_count
                FROM historical_ohlcv_candles
                WHERE chain_id = ? AND pool_address = ? AND timeframe = ? AND timestamp_utc = ?
                  AND price_currency = ? AND volume_currency = ?;
            """, (chain_id, pool_address, timeframe, ts_utc, price_curr, vol_curr)).fetchone()

            if not existing:
                cid = f"c_{chain_id}_{pool_address}_{timeframe}_{ts_unix}_{os.urandom(3).hex()}"
                conn.execute("""
                    INSERT INTO historical_ohlcv_candles (
                        id, chain_id, pool_address, base_token_address, quote_token_address,
                        timeframe, timestamp_utc, timestamp_unix, end_timestamp_utc,
                        open_price, high_price, low_price, close_price, volume,
                        price_currency, volume_currency, bar_status, provider, provider_version,
                        revision_count, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    cid, chain_id, pool_address, base_addr, quote_addr,
                    timeframe, ts_utc, ts_unix, end_utc,
                    op, hp, lp, cp, vol,
                    price_curr, vol_curr, bar_status, provider, provider_ver,
                    0, now_str
                ))
                conn.commit()
                return "NEW_INSERT"

            # Check if identical duplicate
            if (existing["open_price"] == op and
                existing["high_price"] == hp and
                existing["low_price"] == lp and
                existing["close_price"] == cp and
                existing["volume"] == vol):
                return "IDENTICAL_DUPLICATE"

            # Conflicting revision detected -> Log audit event and update
            old_json = json.dumps({
                "open_price": existing["open_price"],
                "high_price": existing["high_price"],
                "low_price": existing["low_price"],
                "close_price": existing["close_price"],
                "volume": existing["volume"]
            })
            new_json = json.dumps({
                "open_price": op,
                "high_price": hp,
                "low_price": lp,
                "close_price": cp,
                "volume": vol
            })
            aid = f"rev_{int(datetime.now(timezone.utc).timestamp())}_{os.urandom(3).hex()}"
            conn.execute("""
                INSERT INTO candle_revision_audit_log (
                    audit_id, candle_id, chain_id, pool_address, timeframe, timestamp_utc,
                    old_ohlcv_json, new_ohlcv_json, reason, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (aid, existing["id"], chain_id, pool_address, timeframe, ts_utc, old_json, new_json, "PROVIDER_REVISION", now_str))

            new_rev_count = existing["revision_count"] + 1
            conn.execute("""
                UPDATE historical_ohlcv_candles
                SET open_price = ?, high_price = ?, low_price = ?, close_price = ?, volume = ?,
                    bar_status = ?, revision_count = ?, ingested_at = ?
                WHERE id = ?;
            """, (op, hp, lp, cp, vol, bar_status, new_rev_count, now_str, existing["id"]))
            conn.commit()
            return "CONFLICTING_UPDATE"

    def upsert_candles_batch(self, candles: List[Dict[str, Any]]) -> Dict[str, int]:
        """Upserts a batch of candles, returning count metrics for all actions."""
        summary = {
            "new_inserts": 0,
            "identical_duplicates": 0,
            "conflicting_updates": 0,
            "rejected_bounds": 0
        }
        for c in candles:
            res = self.upsert_candle(c)
            if res == "NEW_INSERT":
                summary["new_inserts"] += 1
            elif res == "IDENTICAL_DUPLICATE":
                summary["identical_duplicates"] += 1
            elif res == "CONFLICTING_UPDATE":
                summary["conflicting_updates"] += 1
            elif res == "REJECTED_BOUNDS":
                summary["rejected_bounds"] += 1
        return summary

    def get_candles(
        self,
        chain_id: str,
        pool_address: str,
        timeframe: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 100,
        bar_status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieves candles ordered chronologically (timestamp_unix ASC)."""
        pool_addr = pool_address.lower()
        query = """
            SELECT * FROM historical_ohlcv_candles
            WHERE chain_id = ? AND pool_address = ? AND timeframe = ?
        """
        params: List[Any] = [chain_id, pool_addr, timeframe]

        if start_time:
            query += " AND timestamp_utc >= ?"
            params.append(normalize_utc_timestamp(start_time))
        if end_time:
            query += " AND timestamp_utc <= ?"
            params.append(normalize_utc_timestamp(end_time))
        if bar_status:
            query += " AND bar_status = ?"
            params.append(bar_status)

        query += " ORDER BY timestamp_unix ASC LIMIT ?;"
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_candle_count(self, chain_id: str, pool_address: str, timeframe: str) -> int:
        """Returns the total number of candles stored for a given pool and timeframe."""
        pool_addr = pool_address.lower()
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT COUNT(*) FROM historical_ohlcv_candles
                WHERE chain_id = ? AND pool_address = ? AND timeframe = ?;
            """, (chain_id, pool_addr, timeframe)).fetchone()
            return row[0] if row else 0

    def get_candle_revision_audit_logs(
        self,
        chain_id: str,
        pool_address: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Retrieves conflicting revision audit logs."""
        pool_addr = pool_address.lower()
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM candle_revision_audit_log
                WHERE chain_id = ? AND pool_address = ?
                ORDER BY recorded_at DESC LIMIT ?;
            """, (chain_id, pool_addr, limit)).fetchall()
            return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Declarative Strategy SDK Persistence (P1.5-C)
    # -------------------------------------------------------------------------
    def save_declarative_strategy(
        self,
        strategy_id: str,
        name: str,
        description: Optional[str],
        version: str,
        definition_json: str,
        checksum: str,
        change_summary: str = "Initial creation"
    ) -> bool:
        """
        Saves a new declarative strategy and its initial immutable version.
        Raises ValueError if strategy_id already exists.
        """
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        version_id = f"{strategy_id}_{version}"

        with self._get_connection() as conn:
            # Check existence
            existing = conn.execute("SELECT id FROM declarative_strategies WHERE id = ?", (strategy_id,)).fetchone()
            if existing:
                raise ValueError(f"Strategy '{strategy_id}' already exists. Use update endpoint to create a new version.")

            conn.execute("""
                INSERT INTO declarative_strategies (id, name, description, current_version, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'ENABLED', ?, ?);
            """, (strategy_id, name, description, version, now_str, now_str))

            conn.execute("""
                INSERT INTO declarative_strategy_versions (version_id, strategy_id, version, definition_json, checksum, created_at, change_summary, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1);
            """, (version_id, strategy_id, version, definition_json, checksum, now_str, change_summary))
            conn.commit()
            return True

    def update_declarative_strategy_version(
        self,
        strategy_id: str,
        new_version: str,
        definition_json: str,
        checksum: str,
        change_summary: str
    ) -> bool:
        """
        Creates a new immutable version for an existing declarative strategy and updates current_version.
        """
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        version_id = f"{strategy_id}_{new_version}"

        with self._get_connection() as conn:
            existing = conn.execute("SELECT id FROM declarative_strategies WHERE id = ?", (strategy_id,)).fetchone()
            if not existing:
                raise ValueError(f"Strategy '{strategy_id}' not found.")

            # Check if this specific version already exists
            v_existing = conn.execute("SELECT version_id FROM declarative_strategy_versions WHERE version_id = ?", (version_id,)).fetchone()
            if v_existing:
                raise ValueError(f"Version '{new_version}' already exists for strategy '{strategy_id}'. Immutable versions cannot be overwritten.")

            # Deactivate previous active versions
            conn.execute("UPDATE declarative_strategy_versions SET is_active = 0 WHERE strategy_id = ?", (strategy_id,))

            # Insert new version
            conn.execute("""
                INSERT INTO declarative_strategy_versions (version_id, strategy_id, version, definition_json, checksum, created_at, change_summary, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1);
            """, (version_id, strategy_id, new_version, definition_json, checksum, now_str, change_summary))

            # Update parent record
            conn.execute("""
                UPDATE declarative_strategies
                SET current_version = ?, updated_at = ?
                WHERE id = ?;
            """, (new_version, now_str, strategy_id))
            conn.commit()
            return True

    def get_declarative_strategy(
        self,
        strategy_id: str,
        version: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves a declarative strategy.
        If version is omitted, returns the current active version.
        """
        with self._get_connection() as conn:
            parent = conn.execute("SELECT * FROM declarative_strategies WHERE id = ?", (strategy_id,)).fetchone()
            if not parent:
                return None

            target_version = version or parent["current_version"]
            version_row = conn.execute("""
                SELECT * FROM declarative_strategy_versions
                WHERE strategy_id = ? AND version = ?;
            """, (strategy_id, target_version)).fetchone()

            if not version_row:
                return None

            res = dict(parent)
            res["version_record"] = dict(version_row)
            res["definition"] = json.loads(version_row["definition_json"])
            return res

    def list_declarative_strategies(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Lists declarative strategies ordered by updated_at DESC."""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT id, name, description, current_version, status, created_at, updated_at
                FROM declarative_strategies
                ORDER BY updated_at DESC LIMIT ?;
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def set_declarative_strategy_status(self, strategy_id: str, enabled: bool) -> bool:
        """Enables or disables a declarative strategy."""
        status_str = "ENABLED" if enabled else "DISABLED"
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._get_connection() as conn:
            res = conn.execute("""
                UPDATE declarative_strategies
                SET status = ?, updated_at = ?
                WHERE id = ?;
            """, (status_str, now_str, strategy_id))
            conn.commit()
            return res.rowcount > 0

    def get_declarative_strategy_versions(self, strategy_id: str) -> List[Dict[str, Any]]:
        """Retrieves all immutable version records for a strategy."""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT version_id, strategy_id, version, checksum, created_at, change_summary, is_active
                FROM declarative_strategy_versions
                WHERE strategy_id = ?
                ORDER BY created_at DESC;
            """, (strategy_id,)).fetchall()
            return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Strategy Paper Trading Persistence (P1.5-F)
    # -------------------------------------------------------------------------
    def create_strategy_paper_account(
        self,
        account_id: str,
        name: str,
        initial_balance_usd: str
    ) -> bool:
        """Creates a virtual paper trading account for strategy testing."""
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._get_connection() as conn:
            existing = conn.execute("SELECT account_id FROM strategy_paper_accounts WHERE account_id = ?", (account_id,)).fetchone()
            if existing:
                raise ValueError(f"Paper account '{account_id}' already exists.")
            conn.execute("""
                INSERT INTO strategy_paper_accounts (account_id, name, cash_balance_usd, initial_balance_usd, realized_pnl_usd, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, '0', 'ACTIVE', ?, ?);
            """, (account_id, name, initial_balance_usd, initial_balance_usd, now_str, now_str))
            conn.commit()
            return True

    def get_strategy_paper_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a paper account by ID."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM strategy_paper_accounts WHERE account_id = ?", (account_id,)).fetchone()
            return dict(row) if row else None

    def list_strategy_paper_accounts(self) -> List[Dict[str, Any]]:
        """Lists all paper accounts."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM strategy_paper_accounts ORDER BY created_at DESC;").fetchall()
            return [dict(r) for r in rows]

    def update_strategy_paper_account_balances(self, account_id: str, cash_usd: str, realized_pnl_usd: str) -> bool:
        """Updates cash balance and realized PnL of a paper account."""
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._get_connection() as conn:
            res = conn.execute("""
                UPDATE strategy_paper_accounts
                SET cash_balance_usd = ?, realized_pnl_usd = ?, updated_at = ?
                WHERE account_id = ?;
            """, (cash_usd, realized_pnl_usd, now_str, account_id))
            conn.commit()
            return res.rowcount > 0

    def save_strategy_paper_order(self, order: Dict[str, Any]) -> bool:
        """Saves a simulated paper order with idempotency protection."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO strategy_paper_orders (
                    order_id, account_id, strategy_id, chain_id, pool_address, side,
                    quantity, price, fee_usd, slippage_usd, status, idempotency_key, created_at, filled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                order["order_id"], order["account_id"], order["strategy_id"], order["chain_id"],
                order["pool_address"], order["side"], str(order["quantity"]), str(order["price"]),
                str(order["fee_usd"]), str(order["slippage_usd"]), order["status"],
                order["idempotency_key"], order["created_at"], order.get("filled_at")
            ))
            conn.commit()
            return True

    def get_strategy_paper_orders(self, account_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves recent orders for an account."""
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM strategy_paper_orders
                WHERE account_id = ?
                ORDER BY created_at DESC LIMIT ?;
            """, (account_id, limit)).fetchall()
            return [dict(r) for r in rows]

    def save_or_update_strategy_paper_position(self, pos: Dict[str, Any]) -> bool:
        """Saves or updates a virtual position."""
        with self._get_connection() as conn:
            existing = conn.execute("SELECT position_id FROM strategy_paper_positions WHERE position_id = ?", (pos["position_id"],)).fetchone()
            if existing:
                conn.execute("""
                    UPDATE strategy_paper_positions
                    SET quantity = ?, entry_price = ?, cost_basis_usd = ?, realized_pnl_usd = ?, status = ?, closed_at = ?
                    WHERE position_id = ?;
                """, (
                    str(pos["quantity"]), str(pos["entry_price"]), str(pos["cost_basis_usd"]),
                    str(pos["realized_pnl_usd"]), pos["status"], pos.get("closed_at"), pos["position_id"]
                ))
            else:
                conn.execute("""
                    INSERT INTO strategy_paper_positions (
                        position_id, account_id, strategy_id, chain_id, pool_address, side,
                        quantity, entry_price, cost_basis_usd, realized_pnl_usd, status, opened_at, closed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    pos["position_id"], pos["account_id"], pos["strategy_id"], pos["chain_id"],
                    pos["pool_address"], pos.get("side", "BUY"), str(pos["quantity"]), str(pos["entry_price"]),
                    str(pos["cost_basis_usd"]), str(pos["realized_pnl_usd"]), pos["status"],
                    pos["opened_at"], pos.get("closed_at")
                ))
            conn.commit()
            return True

    def get_strategy_paper_positions(self, account_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves positions for an account."""
        query = "SELECT * FROM strategy_paper_positions WHERE account_id = ?"
        params: List[Any] = [account_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY opened_at DESC;"

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]



