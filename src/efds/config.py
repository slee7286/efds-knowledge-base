"""Application configuration loaded from environment variables or ``.env``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings required by the database-backed scripts."""

    database_url: str

    @property
    def sqlalchemy_database_url(self) -> str:
        """Return a SQLAlchemy URL explicitly selecting the psycopg 3 driver."""

        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+psycopg://", 1)
        return self.database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings on first use, failing clearly when the database URL is absent."""

    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is missing. Copy .env.example to .env and add your PostgreSQL connection string."
        )
    return Settings(database_url=database_url)
