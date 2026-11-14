from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Solana Memecoin OS"
    APP_ENV: str = "development"
    DEBUG: bool = True
    APP_PORT: int = 8000
    HOST: str = "127.0.0.1"

    # Runtime Profile & Operating Mode (P10.2-B Lightweight Core)
    PROFILE: str = "lite"  # "lite" (Termux / SQLite-first / low memory) or "full"
    OPERATING_MODE: str = "paper_trading"  # "offline_research", "online_research", "paper_trading", "live_trading"

    # Database Configuration (SQLite default for self-hosted zero-external-service operation)
    DATABASE_BACKEND: str = "sqlite"  # "sqlite" or "postgresql"
    DATABASE_URL: str = Field(default="sqlite:///./data/memecoin_os.db")
    SQLITE_DB_PATH: str = "./data/memecoin_os.db"

    # Optional Orchestrations & External Integrations (default False for pure lightweight core)
    ENABLE_DOCKER: bool = False
    ENABLE_N8N: bool = False
    ENABLE_WINDMILL: bool = False
    ENABLE_HERMES: bool = False
    ENABLE_REDIS: bool = False

    # Security & Network Controls
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8000", "http://127.0.0.1:8000"]

    SOLANA_RPC_URL: str = "https://api.mainnet-beta.solana.com"
    SOLANA_WS_URL: str = "wss://api.mainnet-beta.solana.com"
    SOLANA_COMMITMENT: str = "confirmed"

    DEX_SCREENER_API_BASE: str = "https://api.dexscreener.com"
    GOPLUS_API_BASE: str = "https://api.gopluslabs.io"
    RUGCHECK_API_BASE: str = "https://api.rugcheck.xyz"
    JUPITER_QUOTE_API_BASE: str = "https://quote-api.jup.ag/v6"

    # Strict Safety Constraints
    ALLOW_LIVE_TRADING: bool = False
    LIVE_EXECUTION_AUTHORIZATION_TOKEN: str = "LIVE_AUTH_UNCONFIGURED"
    COLD_VAULT_ADDRESSES: List[str] = []
    BURNER_WALLET_ADDRESSES: List[str] = []

    MAX_TRADE_SOL: float = 0.5
    MAX_DAILY_LOSS_SOL: float = 2.0
    MAX_SLIPPAGE_BPS: int = 250
    MAX_PRICE_IMPACT_BPS: int = 300

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_BACKEND.lower() == "sqlite" or self.DATABASE_URL.startswith("sqlite")

    @property
    def is_offline(self) -> bool:
        return self.OPERATING_MODE == "offline_research"

    @property
    def can_execute_live(self) -> bool:
        return self.ALLOW_LIVE_TRADING and self.OPERATING_MODE == "live_trading"

settings = Settings()

