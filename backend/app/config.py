from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./switchboard.db"
    redis_url: str = "redis://localhost:6379/0"
    demo_processing_delay_seconds: float = 1.5
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
