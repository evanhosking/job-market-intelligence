from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///market.db"
    app_mode: Literal["local", "deployed"] = "local"
    admin_key: str = ""
    web_origin: str = "http://localhost:3000"
    collection_mode: Literal["offline", "live"] = "offline"
    monthly_item_cap: int = Field(default=3000, ge=1, le=100000)
    request_cap: int = Field(default=250, ge=1, le=1000)
    llm_monthly_request_cap: int = Field(default=100, ge=0, le=10000)
    llm_mode: Literal["rules", "live"] = "rules"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_endpoint: str = "https://api.openai.com/v1/chat/completions"

    @model_validator(mode="after")
    def secure(self):
        if self.app_mode == "deployed" and len(self.admin_key) < 32:
            raise ValueError("Deployed mode requires an ADMIN_KEY with at least 32 characters")
        return self
