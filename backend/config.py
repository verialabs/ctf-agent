"""Pydantic Settings — credentials from .env file + environment variables."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings

AllSolvedPolicy = Literal["wait", "exit", "idle"]
WriteupMode = Literal["off", "confirmed", "solved"]


class Settings(BaseSettings):
    # Competition platform
    platform: str = "ctfd"
    platform_url: str = ""
    lingxu_event_id: int = 0
    lingxu_cookie: str = ""
    lingxu_cookie_file: str = ""

    # CTFd
    ctfd_url: str = "http://localhost:8000"
    ctfd_user: str = "admin"
    ctfd_pass: str = "admin"
    ctfd_token: str = ""

    # API Keys
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""

    # Provider-specific (optional, for Bedrock/Azure/Zen fallback)
    aws_region: str = "us-east-1"
    aws_bearer_token: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    opencode_zen_api_key: str = ""

    # Infra
    sandbox_image: str = "ctf-sandbox"
    max_concurrent_challenges: int = 10
    max_attempts_per_challenge: int = 3
    container_memory_limit: str = "16g"
    all_solved_policy: AllSolvedPolicy = "wait"
    all_solved_idle_seconds: int = 300
    writeup_mode: WriteupMode = "off"
    writeup_dir: str = "writeups"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "validate_assignment": True,
    }

    @model_validator(mode="after")
    def validate_all_solved_idle_seconds(self) -> Settings:
        if self.all_solved_policy == "idle" and self.all_solved_idle_seconds <= 0:
            raise ValueError("all_solved_idle_seconds must be greater than 0 when all_solved_policy is idle")
        return self
