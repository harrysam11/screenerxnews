from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Solana Memecoin OS"
    APP_ENV: str = "development"
    DEBUG: bool = True
    APP_PORT: int = 8000

    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://memecoin_user:memecoin_secure_pass@localhost:5432/memecoin_os"
    )

    SOLANA_RPC_URL: str = "https://api.mainnet-beta.solana.com"
    SOLANA_WS_URL: str = "wss://api.mainnet-beta.solana.com"
    SOLANA_COMMITMENT: str = "confirmed"

    DEX_SCREENER_API_BASE: str = "https://api.dexscreener.com"
    GOPLUS_API_BASE: str = "https://api.gopluslabs.io"
    RUGCHECK_API_BASE: str = "https://api.rugcheck.xyz"
    JUPITER_QUOTE_API_BASE: str = "https://quote-api.jup.ag/v6"

    # Strict Safety Constraints
    ALLOW_LIVE_TRADING: bool = False
    COLD_VAULT_ADDRESSES: List[str] = []
    BURNER_WALLET_ADDRESSES: List[str] = []

    MAX_TRADE_SOL: float = 0.5
    MAX_DAILY_LOSS_SOL: float = 2.0
    MAX_SLIPPAGE_BPS: int = 250
    MAX_PRICE_IMPACT_BPS: int = 300

settings = Settings()
