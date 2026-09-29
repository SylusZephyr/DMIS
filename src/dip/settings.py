"""Platform settings from environment variables.

Every external service is optional: when its variable is unset the
platform uses an embedded fallback under ``data/platform/`` so it runs on
a laptop with no services, and scales by configuration only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DIP_DATA_DIR", str(PROJECT_ROOT / "data" / "platform"))))
    lake_dir: Path = field(default_factory=lambda: Path(_env("DIP_LAKE_DIR", str(PROJECT_ROOT / "data" / "lake"))))
    postgres_url: str | None = field(default_factory=lambda: _env("DIP_POSTGRES_URL"))
    duckdb_path: str | None = field(default_factory=lambda: _env("DIP_DUCKDB_PATH"))
    neo4j_uri: str | None = field(default_factory=lambda: _env("DIP_NEO4J_URI"))
    neo4j_user: str = field(default_factory=lambda: _env("DIP_NEO4J_USER", "neo4j"))
    neo4j_password: str | None = field(default_factory=lambda: _env("DIP_NEO4J_PASSWORD"))
    qdrant_url: str | None = field(default_factory=lambda: _env("DIP_QDRANT_URL"))
    qdrant_api_key: str | None = field(default_factory=lambda: _env("DIP_QDRANT_API_KEY"))
    redis_url: str | None = field(default_factory=lambda: _env("DIP_REDIS_URL"))
    cors_origins: tuple[str, ...] = field(default_factory=lambda: tuple(
        _env("DIP_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")))

    @property
    def business_url(self) -> str:
        return self.postgres_url or f"sqlite:///{self.data_dir / 'business.db'}"

    @property
    def analytics_path(self) -> str:
        return self.duckdb_path or str(self.data_dir / "analytics.duckdb")

    def describe(self) -> dict:
        return {
            "business_db": "postgresql" if self.postgres_url else "sqlite (embedded)",
            "analytics_db": "duckdb",
            "graph": "neo4j" if self.neo4j_uri else "embedded (networkx + duckdb)",
            "vectors": "qdrant server" if self.qdrant_url else "qdrant local mode (embedded)",
            "lake": str(self.lake_dir),
            "response_cache": "redis (shared by all API workers)" if self.redis_url else "in-process",
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.lake_dir.mkdir(parents=True, exist_ok=True)
    return s
