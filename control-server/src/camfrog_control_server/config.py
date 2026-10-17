"""Fail-closed environment configuration for the control plane."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    admin_api_token: str
    jwt_signing_secret: str
    docs_enabled: bool = False
    command_lease_seconds: int = 60
    command_max_attempts: int = 3
    dashboard_jwt_ttl_seconds: int = 900
    api_rate_limit_requests: int = 240
    auth_rate_limit_requests: int = 20
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    cors_origins: list[str] = field(default_factory=list)
    trusted_hosts: list[str] = field(default_factory=list)
    zworkforce_integration_token: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not self.database_url.strip():
            raise ValueError("DATABASE_URL is required.")
        if len(self.admin_api_token) < 32:
            raise ValueError("CONTROL_ADMIN_TOKEN must contain at least 32 characters.")
        if len(self.jwt_signing_secret) < 32:
            raise ValueError("CONTROL_JWT_SECRET must contain at least 32 characters.")
        if self.zworkforce_integration_token and not re.fullmatch(
            r"[A-Za-z0-9._~-]{32,512}", self.zworkforce_integration_token
        ):
            raise ValueError(
                "CONTROL_ZWORKFORCE_INTEGRATION_TOKEN must be 32 to 512 URL-safe characters."
            )
        if not 15 <= self.command_lease_seconds <= 600:
            raise ValueError("CONTROL_COMMAND_LEASE_SECONDS must be between 15 and 600.")
        if not 1 <= self.command_max_attempts <= 10:
            raise ValueError("CONTROL_COMMAND_MAX_ATTEMPTS must be between 1 and 10.")
        if not 60 <= self.dashboard_jwt_ttl_seconds <= 3600:
            raise ValueError("CONTROL_DASHBOARD_JWT_TTL must be between 60 and 3600 seconds.")
        if not 1 <= self.api_rate_limit_requests <= 10_000:
            raise ValueError("CONTROL_API_RATE_LIMIT must be between 1 and 10000.")
        if not 1 <= self.auth_rate_limit_requests <= 1000:
            raise ValueError("CONTROL_AUTH_RATE_LIMIT must be between 1 and 1000.")
        valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_log_levels:
            raise ValueError("CONTROL_LOG_LEVEL must be one of DEBUG/INFO/WARNING/ERROR/CRITICAL.")
        if self.log_level != self.log_level.upper():
            object.__setattr__(self, "log_level", self.log_level.upper())
        if self.log_format not in {"json", "text"}:
            raise ValueError("CONTROL_LOG_FORMAT must be 'json' or 'text'.")
        if not isinstance(self.cors_origins, (list, tuple)):
            raise ValueError("cors_origins must be a list or tuple of strings.")
        if isinstance(self.cors_origins, tuple):
            object.__setattr__(self, "cors_origins", list(self.cors_origins))
        if not isinstance(self.trusted_hosts, (list, tuple)):
            raise ValueError("trusted_hosts must be a list or tuple of strings.")
        if isinstance(self.trusted_hosts, tuple):
            object.__setattr__(self, "trusted_hosts", list(self.trusted_hosts))

    @classmethod
    def from_env(cls) -> Settings:
        database_url = os.environ.get("DATABASE_URL", "").strip()
        admin_api_token = os.environ.get("CONTROL_ADMIN_TOKEN", "")
        jwt_signing_secret = os.environ.get("CONTROL_JWT_SECRET", "")
        zworkforce_integration_token = os.environ.get(
            "CONTROL_ZWORKFORCE_INTEGRATION_TOKEN", ""
        ).strip()
        if not database_url:
            raise ValueError("DATABASE_URL is required; no database default is provided.")
        if not admin_api_token:
            raise ValueError("CONTROL_ADMIN_TOKEN is required; no admin token default is provided.")
        if not jwt_signing_secret:
            raise ValueError("CONTROL_JWT_SECRET is required; no JWT signing default is provided.")

        docs_value = os.environ.get("CONTROL_DOCS_ENABLED", "false").strip().lower()
        if docs_value not in {"true", "false"}:
            raise ValueError("CONTROL_DOCS_ENABLED must be 'true' or 'false'.")

        try:
            lease_seconds = int(os.environ.get("CONTROL_COMMAND_LEASE_SECONDS", "60"))
            max_attempts = int(os.environ.get("CONTROL_COMMAND_MAX_ATTEMPTS", "3"))
            jwt_ttl_seconds = int(os.environ.get("CONTROL_DASHBOARD_JWT_TTL", "900"))
            api_rate_limit = int(os.environ.get("CONTROL_API_RATE_LIMIT", "240"))
            auth_rate_limit = int(os.environ.get("CONTROL_AUTH_RATE_LIMIT", "20"))
        except ValueError as exc:
            raise ValueError("Control-plane limits and lifetimes must be integers.") from exc

        log_level = (
            os.environ.get("CONTROL_LOG_LEVEL", os.environ.get("LOG_LEVEL", "INFO")).strip().upper()
        )
        log_format = os.environ.get("CONTROL_LOG_FORMAT", "json").strip().lower()

        raw_cors = os.environ.get("CONTROL_CORS_ORIGINS", "").strip()
        if raw_cors:
            if raw_cors.startswith("["):
                try:
                    cors_origins = [
                        str(item).strip() for item in json.loads(raw_cors) if str(item).strip()
                    ]
                except json.JSONDecodeError:
                    cors_origins = [
                        origin.strip() for origin in raw_cors.split(",") if origin.strip()
                    ]
            else:
                cors_origins = [origin.strip() for origin in raw_cors.split(",") if origin.strip()]
        else:
            cors_origins = []

        raw_hosts = os.environ.get("CONTROL_TRUSTED_HOSTS", "").strip()
        if raw_hosts:
            if raw_hosts.startswith("["):
                try:
                    trusted_hosts = [
                        str(item).strip() for item in json.loads(raw_hosts) if str(item).strip()
                    ]
                except json.JSONDecodeError:
                    trusted_hosts = [host.strip() for host in raw_hosts.split(",") if host.strip()]
            else:
                trusted_hosts = [host.strip() for host in raw_hosts.split(",") if host.strip()]
        else:
            trusted_hosts = []

        return cls(
            database_url=database_url,
            admin_api_token=admin_api_token,
            jwt_signing_secret=jwt_signing_secret,
            zworkforce_integration_token=zworkforce_integration_token,
            docs_enabled=docs_value == "true",
            command_lease_seconds=lease_seconds,
            command_max_attempts=max_attempts,
            dashboard_jwt_ttl_seconds=jwt_ttl_seconds,
            api_rate_limit_requests=api_rate_limit,
            auth_rate_limit_requests=auth_rate_limit,
            log_level=log_level,
            log_format=log_format,
            cors_origins=cors_origins,
            trusted_hosts=trusted_hosts,
        )
