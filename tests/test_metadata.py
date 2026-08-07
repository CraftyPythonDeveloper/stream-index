"""
Unit tests for metadata resolution (app/services/metadata.py).

All HTTP calls are mocked with respx so no real network requests are made.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.metadata import resolve_imdb


@pytest.mark.asyncio
async def test_resolve_movie_success():
    """resolve_imdb correctly maps an IMDb movie ID to title + year."""
    fake_response = {
        "movie_results": [
            {
                "title": "Inception",
                "original_title": "Inception",
                "release_date": "2010-07-16",
            }
        ],
        "tv_results": [],
    }

    with respx.mock(base_url="https://wild-surf-4a0d.phisher1.workers.dev") as mock:
        mock.get("/find/tt1375666").mock(return_value=httpx.Response(200, json=fake_response))

        async with httpx.AsyncClient() as client:
            meta = await resolve_imdb(client, "tt1375666", "movie")

    assert meta is not None
    assert meta.title == "Inception"
    assert meta.year == 2010
    assert meta.media_type == "movie"
    assert meta.imdb_id == "tt1375666"


@pytest.mark.asyncio
async def test_resolve_series_success():
    """resolve_imdb correctly maps an IMDb series ID with season/episode."""
    fake_response = {
        "movie_results": [],
        "tv_results": [
            {
                "name": "Breaking Bad",
                "original_name": "Breaking Bad",
                "first_air_date": "2008-01-20",
            }
        ],
    }

    with respx.mock(base_url="https://wild-surf-4a0d.phisher1.workers.dev") as mock:
        mock.get("/find/tt0903747").mock(return_value=httpx.Response(200, json=fake_response))

        async with httpx.AsyncClient() as client:
            meta = await resolve_imdb(client, "tt0903747", "series", season=1, episode=3)

    assert meta is not None
    assert meta.title == "Breaking Bad"
    assert meta.year == 2008
    assert meta.media_type == "series"
    assert meta.season == 1
    assert meta.episode == 3


@pytest.mark.asyncio
async def test_resolve_no_results():
    """resolve_imdb returns None when TMDB finds nothing."""
    fake_response = {"movie_results": [], "tv_results": []}

    with respx.mock(base_url="https://wild-surf-4a0d.phisher1.workers.dev") as mock:
        mock.get("/find/tt9999999").mock(return_value=httpx.Response(200, json=fake_response))

        async with httpx.AsyncClient() as client:
            meta = await resolve_imdb(client, "tt9999999", "movie")

    assert meta is None


@pytest.mark.asyncio
async def test_resolve_http_error():
    """resolve_imdb returns None on HTTP error without raising."""
    with respx.mock(base_url="https://wild-surf-4a0d.phisher1.workers.dev") as mock:
        mock.get("/find/tt0000001").mock(return_value=httpx.Response(500))

        async with httpx.AsyncClient() as client:
            meta = await resolve_imdb(client, "tt0000001", "movie")

    assert meta is None


@pytest.mark.asyncio
async def test_resolve_missing_year():
    """resolve_imdb handles missing release_date gracefully."""
    fake_response = {
        "movie_results": [
            {"title": "Unknown Film", "release_date": ""}
        ],
        "tv_results": [],
    }

    with respx.mock(base_url="https://wild-surf-4a0d.phisher1.workers.dev") as mock:
        mock.get("/find/tt1111111").mock(return_value=httpx.Response(200, json=fake_response))

        async with httpx.AsyncClient() as client:
            meta = await resolve_imdb(client, "tt1111111", "movie")

    assert meta is not None
    assert meta.title == "Unknown Film"
    assert meta.year is None
