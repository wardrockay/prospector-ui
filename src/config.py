"""
Configuration Management
========================

Centralized configuration with environment-based settings.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    """Application environment."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class FirestoreConfig(BaseSettings):
    """Firestore configuration."""
    
    model_config = SettingsConfigDict(env_prefix="FIRESTORE_", extra="ignore")
    
    drafts_collection: str = Field(default="email_drafts")
    followups_collection: str = Field(default="email_followups")
    opens_collection: str = Field(default="email_opens")


class ServicesConfig(BaseSettings):
    """External services configuration."""
    
    model_config = SettingsConfigDict(extra="ignore")
    
    draft_creator_url: str = Field(
        default="https://draft-creator-642098175556.europe-west1.run.app",
        alias="DRAFT_CREATOR_URL"
    )
    mail_writer_url: str = Field(
        default="https://mail-writer-642098175556.europe-west1.run.app",
        alias="MAIL_WRITER_URL"
    )
    auto_followup_url: str = Field(
        default="https://auto-followup-642098175556.europe-west1.run.app",
        alias="AUTO_FOLLOWUP_URL"
    )


class AppSettings(BaseSettings):
    """Main application settings."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
    
    # Core settings
    environment: Environment = Field(default=Environment.DEVELOPMENT)
    debug: bool = Field(default=False)
    port: int = Field(default=8080, ge=1, le=65535)
    secret_key: str = Field(default="dev-secret-key-change-in-prod")
    
    # GCP settings
    gcp_project_id: str = Field(
        default="light-and-shutter",
        alias="GCP_PROJECT_ID"
    )
    
    # Pagination
    default_page_size: int = Field(default=20, ge=1, le=100)
    
    # Nested configs
    firestore: FirestoreConfig = Field(default_factory=FirestoreConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    
    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION


@lru_cache()
def get_settings() -> AppSettings:
    """Get cached application settings."""
    return AppSettings()


settings = get_settings()
