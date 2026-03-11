from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="OVERMIND_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(default="OverMindAgent")
    app_env: str = Field(default="local")
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    reload: bool = Field(default=False)
    log_level: str = Field(default="info")


@lru_cache
def get_settings() -> Settings:
    return Settings()
