"""Runtime configuration. Everything deployment-specific comes from the environment.

No domain name is hardcoded: set MF_APP_HOSTS and MF_SITES_BASE_DOMAIN to move the
platform to a different domain without touching application code.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_OFFERS = [
    {
        "code": "consult",
        "name": "Working session",
        "price_cents": 0,
        "billing": "free",
        "summary": "A 30-minute conversation about your assessment. No payment, no obligation.",
        "includes": [
            "Review of what you told us and what we could verify",
            "One or two concrete next steps you can take with or without us",
        ],
    },
    {
        "code": "launch",
        "name": "Website and Inquiry Desk setup",
        "price_cents": 19900,
        "billing": "one_time",
        "summary": "A professional website for your business, connected to a private workspace where every inquiry lands.",
        "includes": [
            "Your business website, reviewed and approved by you before it goes live",
            "An inquiry desk: every request in one place with follow-up reminders",
            "A written assessment of where business may be getting missed",
        ],
    },
    {
        "code": "operate",
        "name": "Ongoing workspace",
        "price_cents": 9900,
        "billing": "monthly",
        "summary": "Keep the workspace running: website changes, follow-up tracking, documents and measured improvements.",
        "includes": [
            "Website edits with version history and one-click rollback",
            "Team access with owner, admin, staff and read-only roles",
            "A running record of what was improved and what the numbers show",
        ],
    },
]


class Settings(BaseSettings):
    # A local .env is read for development convenience. MF_ENV_FILE="" turns that off
    # (the test suite does this so a developer's .env can never leak into test results).
    model_config = SettingsConfigDict(env_prefix="MF_", env_file=os.environ.get("MF_ENV_FILE", ".env") or None,
                                      extra="ignore")

    env: str = "development"  # development | test | staging | production
    database_url: str = Field(default="", validation_alias="DATABASE_URL")
    secret_key: str = "dev-only-secret-change-me"

    # Hostnames that serve the marketing site + private app. Anything else is treated
    # as a customer website and resolved through the site_hosts table.
    app_hosts: str = "localhost,127.0.0.1,testserver"
    # Customer sites are published at <slug>.<sites_base_domain>.
    sites_base_domain: str = "sites.localhost"
    # Used when building absolute links in emails. Example: https://app.example.com
    public_base_url: str = "http://localhost:8000"
    brand_name: str = "MeasuredField"
    support_email: str = ""
    # Set when the first publicly accessible VideoObject is deployed.
    # Leaving this blank keeps pre-launch / test pages from claiming a publication date.
    video_publication_date: str = ""

    session_ttl_hours: int = 12
    session_idle_minutes: int = 120
    invite_ttl_hours: int = 72
    reset_ttl_minutes: int = 45
    deletion_grace_hours: int = 72

    # Jobs: run inline after the request commits (single-service deployments, tests)
    # or leave them for the worker process.
    inline_jobs: bool = False

    email_provider: str = "none"  # none | resend
    email_from: str = ""
    resend_api_key: str = ""

    payment_provider: str = "none"  # none | stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    ai_provider: str = "none"  # none | anthropic  (no model calls are made when "none")
    ai_monthly_budget_usd: float = 0.0

    offers_json: str = ""
    terms_version: str = "2026-11-draft"
    max_upload_bytes: int = 5 * 1024 * 1024
    # Deprecated/disabled app-layer flag: arbitrary forwarded headers are ignored by the app,
    # and setting MF_TRUST_PROXY_HEADERS=true is rejected in staging/production validation.
    # Reverse-proxy client IP handling must be solved via verified ASGI trusted proxies
    # and/or edge rate limiting (rate limiting across a proxy is not production-ready in-app).
    trust_proxy_headers: bool = False
    # Number of reverse proxies known to be in front of the app (0 = ignore
    # X-Forwarded-For). Only the entry written by our own outermost trusted proxy is used.
    trusted_proxy_hops: int = 0
    log_level: str = "INFO"

    @field_validator("video_publication_date")
    @classmethod
    def _validate_video_publication_date(cls, value: str) -> str:
        from datetime import datetime
        if value:
            try:
                date = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if date.tzinfo is None or date.utcoffset() is None:
                    raise ValueError("Video publication date must include a timezone")
            except ValueError as exc:
                raise ValueError("MF_VIDEO_PUBLICATION_DATE must be an ISO-8601 timestamp with timezone") from exc
        return value

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Render provides postgres:// or postgresql:// ; SQLAlchemy needs the driver name.
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://"):]
        return v

    @property
    def is_production(self) -> bool:
        return self.env in ("production", "staging")

    @property
    def app_host_set(self) -> set[str]:
        return {h.strip().lower() for h in self.app_hosts.split(",") if h.strip()}

    @property
    def offers(self) -> list[dict]:
        if self.offers_json:
            return json.loads(self.offers_json)
        return DEFAULT_OFFERS

    def offer(self, code: str) -> dict | None:
        return next((o for o in self.offers if o["code"] == code), None)

    def validate_for_production(self) -> list[str]:
        """Return configuration problems that must block a production start."""
        problems = []
        if not self.database_url.startswith("postgresql+psycopg://"):
            problems.append("DATABASE_URL must point to PostgreSQL.")
        if self.is_production:
            if len(self.secret_key) < 32 or self.secret_key.startswith("dev-only"):
                problems.append("MF_SECRET_KEY must be a random value of at least 32 characters.")
            if not self.public_base_url.startswith("https://"):
                problems.append("MF_PUBLIC_BASE_URL must be an https:// URL.")
            if {"localhost", "127.0.0.1", "testserver"} & self.app_host_set:
                problems.append("MF_APP_HOSTS must list only real hostnames in production.")
            if self.payment_provider == "stripe" and not (self.stripe_secret_key and self.stripe_webhook_secret):
                problems.append("Stripe is selected but STRIPE keys are missing.")
            if self.email_provider == "resend" and not (self.resend_api_key and self.email_from):
                problems.append("Resend is selected but MF_RESEND_API_KEY or MF_EMAIL_FROM is missing.")
            if self.trust_proxy_headers:
                problems.append(
                    "MF_TRUST_PROXY_HEADERS=true is not supported: unverified X-Forwarded-For headers "
                    "are ignored at the application layer to prevent rate-limit bypass. Configure "
                    "verified ASGI trusted proxies and/or edge rate limiting instead."
                )
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
