"""
Unit tests for the HDHub4u provider (app/providers/hdhub4u.py).

All HTTP calls are mocked with respx.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.models import ResolvedMeta
from app.providers.hdhub4u import HDHub4uProvider, _b64decode, _rot13


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

PINGORA_BASE = "https://search.pingora.fyi"


def make_meta(
    title: str = "Inception",
    year: int | None = 2010,
    media_type: str = "movie",
    season: int | None = None,
    episode: int | None = None,
) -> ResolvedMeta:
    return ResolvedMeta(
        title=title,
        year=year,
        media_type=media_type,
        imdb_id="tt1375666",
        season=season,
        episode=episode,
    )


FAKE_SEARCH_RESPONSE = {
    "hits": [
        {
            "document": {
                "post_title": "Inception (2010) 1080p BluRay",
                "permalink": "https://hdhub4u.rehab/inception-2010/",
                "post_thumbnail": "https://example.com/thumb.jpg",
                "category": ["Hollywood"],
                "id": "1234",
                "post_date": "2024-01-01",
                "post_type": "post",
                "sort_by_date": 1704067200,
            },
            "highlight": {},
            "highlights": [],
            "text_match": 100,
            "text_match_info": {
                "best_field_score": "100",
                "best_field_weight": 4,
                "fields_matched": 1,
                "num_tokens_dropped": 0,
                "score": "100",
                "tokens_matched": 2,
                "typo_prefix_score": 0,
            },
        }
    ],
    "found": 1,
    "out_of": 100,
    "page": 1,
    "request_params": {
        "collection_name": "post",
        "first_q": "Inception 2010",
        "per_page": 10,
        "q": "Inception 2010",
    },
    "search_cutoff": False,
    "search_time_ms": 5,
}


# ---------------------------------------------------------------------------
# Tests — Search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_returns_permalink():
    """search() returns the permalink from a successful Pingora response."""
    with respx.mock(base_url=PINGORA_BASE) as mock:
        mock.get("/collections/post/documents/search").mock(
            return_value=httpx.Response(200, json=FAKE_SEARCH_RESPONSE)
        )
        async with httpx.AsyncClient() as client:
            provider = HDHub4uProvider(client)
            meta = make_meta()
            results = await provider.search(meta)

    assert results == ["https://hdhub4u.rehab/inception-2010/"]


@pytest.mark.asyncio
async def test_search_empty_hits():
    """search() returns [] when Pingora has no hits."""
    empty_response = {**FAKE_SEARCH_RESPONSE, "hits": [], "found": 0}

    with respx.mock(base_url=PINGORA_BASE) as mock:
        mock.get("/collections/post/documents/search").mock(
            return_value=httpx.Response(200, json=empty_response)
        )
        async with httpx.AsyncClient() as client:
            provider = HDHub4uProvider(client)
            results = await provider.search(make_meta())

    assert results == []


@pytest.mark.asyncio
async def test_search_http_error():
    """search() returns [] on HTTP error without raising."""
    with respx.mock(base_url=PINGORA_BASE) as mock:
        mock.get("/collections/post/documents/search").mock(
            return_value=httpx.Response(503)
        )
        async with httpx.AsyncClient() as client:
            provider = HDHub4uProvider(client)
            results = await provider.search(make_meta())

    assert results == []


# ---------------------------------------------------------------------------
# Tests — Movie stream extraction
# ---------------------------------------------------------------------------

FAKE_MOVIE_PAGE = """
<html><body>
<div class="page-body">
  <h3><a href="https://hubcloud.foo/file/abc123">Download 1080p</a></h3>
  <h4><a href="https://hubcloud.foo/file/def456">Download 720p</a></h4>
  <div>
    <a href="https://pixeldrain.com/u/xyz789">Pixeldrain</a>
  </div>
</div>
</body></html>
"""

FAKE_HUBCLOUD_ENTRY_PAGE = """
<html><body>
  <a id="download" href="https://hubcloud.foo/hubcloud.php?id=abc123">Click</a>
</body></html>
"""

FAKE_HUBCLOUD_DL_PAGE = """
<html><body>
  <div class="card-header">Inception (2010) 1080p WEB-DL</div>
  <i id="size">2.1 GB</i>
  <a class="btn" href="https://fsl.example.com/dl/abc">FSL Server</a>
  <a class="btn" href="https://pixeldrain.com/u/aaabbb">Pixeldrain Server</a>
</body></html>
"""


@pytest.mark.asyncio
async def test_get_movie_streams_hubcloud():
    """get_streams() for a movie finds HubCloud links and extracts FSL + Pixeldrain."""
    with respx.mock() as mock:
        # Movie page
        mock.get("https://hdhub4u.rehab/inception-2010/").mock(
            return_value=httpx.Response(200, text=FAKE_MOVIE_PAGE)
        )
        # HubCloud entry page
        mock.get("https://hubcloud.foo/file/abc123").mock(
            return_value=httpx.Response(200, text=FAKE_HUBCLOUD_ENTRY_PAGE)
        )
        mock.get("https://hubcloud.foo/file/def456").mock(
            return_value=httpx.Response(200, text=FAKE_HUBCLOUD_ENTRY_PAGE)
        )
        # HubCloud download page
        mock.get("https://hubcloud.foo/hubcloud.php?id=abc123").mock(
            return_value=httpx.Response(200, text=FAKE_HUBCLOUD_DL_PAGE)
        )

        async with httpx.AsyncClient() as client:
            provider = HDHub4uProvider(client)
            meta = make_meta()
            streams = await provider.get_streams("https://hdhub4u.rehab/inception-2010/", meta)

    # Should have at least one stream
    assert len(streams) >= 1
    urls = [s.url for s in streams]
    # FSL link should be present
    assert any("fsl.example.com" in u for u in urls)


@pytest.mark.asyncio
async def test_get_movie_streams_pixeldrain_direct():
    """get_streams() correctly converts Pixeldrain page URLs to API download URLs."""
    async with httpx.AsyncClient() as client:
        provider = HDHub4uProvider(client)
        result = provider._build_pixeldrain_url("https://pixeldrain.com/u/xyz789")
    assert result == "https://pixeldrain.com/api/file/xyz789?download"


# ---------------------------------------------------------------------------
# Tests — Utility functions
# ---------------------------------------------------------------------------


def test_rot13():
    """ROT13 round-trips correctly."""
    text = "Hello, World!"
    assert _rot13(_rot13(text)) == text
    assert _rot13("abc") == "nop"
    assert _rot13("ABC") == "NOP"


def test_b64decode_standard():
    """b64decode handles standard base64 with padding."""
    import base64
    raw = "Hello stream-index"
    encoded = base64.b64encode(raw.encode()).decode()
    assert _b64decode(encoded) == raw


def test_b64decode_no_padding():
    """b64decode adds missing padding automatically."""
    # "Inception" in base64 without padding
    import base64
    raw = "Inception"
    encoded = base64.b64encode(raw.encode()).decode().rstrip("=")
    assert _b64decode(encoded) == raw


def test_detect_quality():
    """_detect_quality extracts quality labels from text."""
    provider_cls = HDHub4uProvider
    # Access static method via class
    assert provider_cls._detect_quality("Inception.2010.1080p.WEB-DL") == "1080p"
    assert provider_cls._detect_quality("Breaking.Bad.S01.720p") == "720p"
    assert provider_cls._detect_quality("Movie.4K.UHD") == "4K"
    assert provider_cls._detect_quality("no quality here") is None


def test_is_hosting_link():
    """_is_hosting_link identifies known hosting domains."""
    assert HDHub4uProvider._is_hosting_link("https://hubcloud.foo/file/123")
    assert HDHub4uProvider._is_hosting_link("https://pixeldrain.com/u/abc")
    assert HDHub4uProvider._is_hosting_link("https://hubdrive.space/file/xyz")
    assert not HDHub4uProvider._is_hosting_link("https://imdb.com/title/tt1375666")


def test_parse_stream_id_movie():
    """parse_stream_id correctly parses a movie ID."""
    from app.main import parse_stream_id
    imdb_id, media_type, season, episode = parse_stream_id("tt1375666", "movie")
    assert imdb_id == "tt1375666"
    assert media_type == "movie"
    assert season is None
    assert episode is None


def test_parse_stream_id_series():
    """parse_stream_id correctly parses a series ID with season:episode."""
    from app.main import parse_stream_id
    imdb_id, media_type, season, episode = parse_stream_id("tt0903747:2:5", "series")
    assert imdb_id == "tt0903747"
    assert media_type == "series"
    assert season == 2
    assert episode == 5
