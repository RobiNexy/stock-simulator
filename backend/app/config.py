import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    cache_dir: Path
    seed_cache_dir: Path
    database_path: Path
    static_dir: Path
    session_cookie: str
    session_days: int
    secure_cookies: bool
    admin_username: str
    admin_password: str | None


@lru_cache
def get_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", BACKEND_DIR / "data"))
    cache_dir = Path(os.environ.get("CACHE_DIR", data_dir / "cache"))
    seed_cache_dir = Path(
        os.environ.get("SEED_CACHE_DIR", BACKEND_DIR / "data" / "seed_cache")
    )
    database_path = Path(
        os.environ.get("DATABASE_PATH", data_dir / "stock-simulator.db")
    )
    static_dir = Path(os.environ.get("STATIC_DIR", PROJECT_DIR / "frontend" / "dist"))
    return Settings(
        data_dir=data_dir,
        cache_dir=cache_dir,
        seed_cache_dir=seed_cache_dir,
        database_path=database_path,
        static_dir=static_dir,
        session_cookie=os.environ.get("SESSION_COOKIE", "stock_sim_session"),
        session_days=_env_int("SESSION_DAYS", 30),
        secure_cookies=_env_bool("SECURE_COOKIES", False),
        admin_username=os.environ.get("ADMIN_USERNAME", "admin"),
        admin_password=os.environ.get("ADMIN_PASSWORD") or None,
    )
