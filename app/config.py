"""
Configuration management for the stream-index Stremio addon.

Uses python-dotenv to load a .env file, then reads environment variables
with typed defaults. No pydantic required — keeps Python 3.14 beta compatible.

All settings can be overridden by environment variables (case-insensitive
on Windows; lowercase keys map to uppercase env var names by convention).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from the project root (silently ignored if it doesn't exist)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, override=False)


def _str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


class Settings:
    """
    Centralised configuration object.

    Read from environment variables (or .env file).
    Import the singleton ``settings`` instead of instantiating this directly.
    """

    # --- Server ---
    addon_host: str = _str("ADDON_HOST", "0.0.0.0")
    # Many PaaS providers (Render, Heroku) inject $PORT dynamically
    addon_port: int = _int("ADDON_PORT", _int("PORT", 7000))
    addon_public_url: str = _str("ADDON_PUBLIC_URL", "")

    # --- Logging ---
    log_level: str = _str("LOG_LEVEL", "INFO")
    uvicorn_log_level: str = _str("UVICORN_LOG_LEVEL", "info")

    # --- HTTP client ---
    request_timeout: float = _float("REQUEST_TIMEOUT", 20.0)
    user_agent: str = _str(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    )

    # --- HDHub4u ---
    hdhub4u_base_url: str = _str("HDHUB4U_BASE_URL", "https://hdhub4u.rehab")
    hdhub4u_search_url: str = _str(
        "HDHUB4U_SEARCH_URL",
        "https://search.pingora.fyi/collections/post/documents/search",
    )
    hdhub4u_domains_url: str = _str(
        "HDHUB4U_DOMAINS_URL",
        "https://raw.githubusercontent.com/phisher98/TVVVV/refs/heads/main/domains.json",
    )

    # --- TMDB metadata ---
    tmdb_api_key: str = _str("TMDB_API_KEY", "1865f43a0549ca50d341dd9ab8b29f49")
    tmdb_base_url: str = _str(
        "TMDB_BASE_URL",
        "https://wild-surf-4a0d.phisher1.workers.dev",
    )



    # --- Addon identity ---
    addon_id: str = _str("ADDON_ID", "community.stremio.stream-index")
    addon_name: str = _str("ADDON_NAME", "Stream Index")
    addon_version: str = _str("ADDON_VERSION", "0.1.0")
    addon_description: str = _str("ADDON_DESCRIPTION", "Search HDHub4u for movies and TV shows.")


# Singleton — import this everywhere
settings = Settings()
