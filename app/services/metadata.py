"""
TMDB-based metadata resolution service.

Converts an IMDb ID into a searchable title + year tuple.
Used by the stream handler before delegating to any provider.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.models import ResolvedMeta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resolve_imdb(
    client: httpx.AsyncClient,
    imdb_id: str,
    media_type: str,
    season: int | None = None,
    episode: int | None = None,
) -> ResolvedMeta | None:
    """
    Resolve an IMDb ID to a canonical title and year via TMDB.

    Args:
        client:     Shared httpx async client.
        imdb_id:    Raw IMDb ID, e.g. "tt1234567".
        media_type: "movie" or "series".
        season:     Season number for TV requests (from Stremio id).
        episode:    Episode number for TV requests (from Stremio id).

    Returns:
        ResolvedMeta on success, None on failure.
    """
    logger.info("Resolving IMDb ID %s (type=%s)", imdb_id, media_type)

    try:
        data = await _find_by_imdb(client, imdb_id, media_type)
    except Exception:
        logger.exception("TMDB find request failed for %s", imdb_id)
        return None

    if data is None:
        logger.warning("No TMDB result for %s", imdb_id)
        return None

    logger.info(
        "Resolved %s → title=%r year=%s",
        imdb_id,
        data["title"],
        data["year"],
    )

    return ResolvedMeta(
        title=data["title"],
        year=data["year"],
        media_type=media_type,
        imdb_id=imdb_id,
        season=season,
        episode=episode,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _find_by_imdb(
    client: httpx.AsyncClient,
    imdb_id: str,
    media_type: str,
) -> dict | None:
    """Call TMDB /find endpoint and extract title + year."""
    url = (
        f"{settings.tmdb_base_url}/find/{imdb_id}"
        f"?api_key={settings.tmdb_api_key}&external_source=imdb_id"
    )
    resp = await client.get(url)
    resp.raise_for_status()
    body = resp.json()

    if media_type == "movie":
        results = body.get("movie_results", [])
        if not results:
            return None
        item = results[0]
        raw_date = item.get("release_date", "")
        return {
            "title": item.get("title") or item.get("original_title", ""),
            "year": _parse_year(raw_date),
        }
    else:
        results = body.get("tv_results", [])
        if not results:
            return None
        item = results[0]
        raw_date = item.get("first_air_date", "")
        return {
            "title": item.get("name") or item.get("original_name", ""),
            "year": _parse_year(raw_date),
        }


def _parse_year(date_str: str) -> int | None:
    """Extract 4-digit year from a YYYY-MM-DD date string."""
    if date_str and len(date_str) >= 4:
        try:
            return int(date_str[:4])
        except ValueError:
            pass
    return None
