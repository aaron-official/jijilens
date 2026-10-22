"""Settings from the environment (.env) and the collection plan (config/collect.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "collect.toml"
MIGRATIONS_DIR = PROJECT_ROOT / "db" / "migrations"


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_dir: Path


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_settings() -> Settings:
    """Real environment variables win over .env, which wins over the defaults."""
    env = {**_read_dotenv(PROJECT_ROOT / ".env"), **os.environ}
    data_dir = Path(env.get("DATA_DIR", "data"))
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    return Settings(
        database_url=env.get("DATABASE_URL", "postgresql://jijilens:jijilens@localhost:5433/jijilens"),
        data_dir=data_dir,
    )


@dataclass(frozen=True)
class Category:
    key: str  # our name for it: the database key and the image folder
    label: str
    jiji_slug: str  # jiji.ug/<jiji_slug>
    # Listing API filters that narrow the Jiji category, e.g. {"filter_attr_272_type": "TVs"}
    filters: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HttpConfig:
    page_delay_seconds: tuple[float, float] = (2.0, 5.0)
    image_delay_seconds: tuple[float, float] = (0.5, 1.5)
    timeout_seconds: float = 30.0
    max_retries: int = 4
    stop_after_consecutive_failures: int = 8
    user_agent: str = "Mozilla/5.0"


@dataclass(frozen=True)
class CollectConfig:
    base_url: str
    categories: list[Category]
    http: HttpConfig
    max_listings_per_category: int = 300
    max_pages_per_category: int = 30
    max_images_per_listing: int = 5
    min_image_side: int = 200

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG) -> "CollectConfig":
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        collect = data.get("collect", {})
        http = data.get("http", {})
        categories = [_category(c) for c in data.get("categories", [])]
        if not categories:
            raise ValueError(f"{path}: no categories")
        keys = [c.key for c in categories]
        if len(keys) != len(set(keys)):
            raise ValueError(f"{path}: category keys must be unique: {keys}")
        return cls(
            base_url=data.get("base_url", "https://jiji.ug").rstrip("/"),
            categories=categories,
            max_listings_per_category=int(collect.get("max_listings_per_category", 300)),
            max_pages_per_category=int(collect.get("max_pages_per_category", 30)),
            max_images_per_listing=int(collect.get("max_images_per_listing", 5)),
            min_image_side=int(collect.get("min_image_side", 200)),
            http=HttpConfig(
                page_delay_seconds=_pair(http.get("page_delay_seconds", [2.0, 5.0])),
                image_delay_seconds=_pair(http.get("image_delay_seconds", [0.5, 1.5])),
                timeout_seconds=float(http.get("timeout_seconds", 30)),
                max_retries=int(http.get("max_retries", 4)),
                stop_after_consecutive_failures=int(http.get("stop_after_consecutive_failures", 8)),
                user_agent=str(http.get("user_agent", HttpConfig.user_agent)),
            ),
        )


def _category(entry: dict) -> Category:
    key = entry.get("key", entry["slug"])
    return Category(
        key=key,
        label=entry.get("label", key),
        jiji_slug=entry["slug"],
        filters={str(k): str(v) for k, v in entry.get("filters", {}).items()},
    )


def _pair(value) -> tuple[float, float]:
    low, high = value
    return float(low), float(high)
