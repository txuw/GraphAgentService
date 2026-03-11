from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseModel):
    app_name: str
    app_env: str
    host: str
    port: int
    reload: bool
    log_level: str


class LLMSettings(BaseModel):
    api_key: SecretStr | None = None
    base_url: str | None = None
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    timeout: float = 60.0
    max_tokens: int | None = None


class GraphSettings(BaseModel):
    default_name: str = "text-analysis"
    debug: bool = False
    enable_structured_output: bool = True
    checkpoint_mode: Literal["memory", "disabled"] = "memory"


class ObservabilitySettings(BaseModel):
    log_payloads: bool = False


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

    llm_api_key: SecretStr | None = Field(default=None)
    llm_base_url: str | None = Field(default=None)
    llm_model: str = Field(default="gpt-4o-mini")
    llm_temperature: float = Field(default=0.0, ge=0, le=2)
    llm_timeout: float = Field(default=60.0, gt=0)
    llm_max_tokens: int | None = Field(default=None, ge=1)

    graph_default_name: str = Field(default="text-analysis")
    graph_debug: bool = Field(default=False)
    graph_enable_structured_output: bool = Field(default=True)
    graph_checkpoint_mode: Literal["memory", "disabled"] = Field(default="memory")

    observability_log_payloads: bool = Field(default=False)

    @property
    def app(self) -> AppSettings:
        return AppSettings(
            app_name=self.app_name,
            app_env=self.app_env,
            host=self.host,
            port=self.port,
            reload=self.reload,
            log_level=self.log_level,
        )

    @property
    def llm(self) -> LLMSettings:
        return LLMSettings(
            api_key=self.llm_api_key,
            base_url=self.llm_base_url,
            model=self.llm_model,
            temperature=self.llm_temperature,
            timeout=self.llm_timeout,
            max_tokens=self.llm_max_tokens,
        )

    @property
    def graph(self) -> GraphSettings:
        return GraphSettings(
            default_name=self.graph_default_name,
            debug=self.graph_debug,
            enable_structured_output=self.graph_enable_structured_output,
            checkpoint_mode=self.graph_checkpoint_mode,
        )

    @property
    def observability(self) -> ObservabilitySettings:
        return ObservabilitySettings(log_payloads=self.observability_log_payloads)


@lru_cache
def get_settings() -> Settings:
    return Settings()
