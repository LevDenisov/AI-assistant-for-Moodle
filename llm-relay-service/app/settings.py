from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "sqlite:///./relay.db"
    llm_api_url: str = "http://llm-host:8000"
    llm_api_key: str | None = None

    public_base_url: str = "http://localhost:8080"

    callback_hmac_secret: str = "dev-secret"
    callback_max_retries: int = 6
    callback_backoff_seconds: float = 2.0

    request_timeout_seconds: float = 60.0

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

settings = Settings()
