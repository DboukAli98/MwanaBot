from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    google_api_key: str = Field(..., alias="GOOGLE_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")

    pinecone_api_key: str | None = Field(default=None, alias="PINECONE_API_KEY")
    pinecone_index_name: str = Field(default="edufrais-mwanabot", alias="PINECONE_INDEX_NAME")
    pinecone_cloud: str = Field(default="aws", alias="PINECONE_CLOUD")
    pinecone_region: str = Field(default="us-east-1", alias="PINECONE_REGION")

    # Default to the Azure production deployment so the bot works
    # out-of-the-box. Override with SCHOOLFEES_API_BASE_URL=http://localhost:5149/api
    # for local backend development.
    schoolfees_api_base_url: str = Field(
        default="https://edufrais-cnatavfte0fhdfe2.francecentral-01.azurewebsites.net/api",
        alias="SCHOOLFEES_API_BASE_URL",
    )
    # Service-account / fallback token. Normally each request brings its
    # own user JWT via metadata.auth_token; this is only a last-resort
    # fallback for testing and is generally None in production.
    schoolfees_api_token: str | None = Field(default=None, alias="SCHOOLFEES_API_TOKEN")
    # Per-request HTTP timeout for SchoolFees calls (seconds). Tools
    # need to fail fast — if SchoolFees hangs the user is staring at a
    # silent chat box.
    schoolfees_api_timeout: float = Field(default=15.0, alias="SCHOOLFEES_API_TIMEOUT")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

