"""Configuration for codiff."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    REPO_CLONE_DIR: str = "/tmp/repos"
    MAX_REPO_SIZE_MB: int = 500

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")


settings = Settings()
