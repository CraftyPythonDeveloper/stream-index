"""
Typed data models shared across the addon.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel
from dataclasses import dataclass, field


@dataclass
class StreamResult(BaseModel):
    """A playable stream returned to Stremio."""

    title: str
    """Shown in the Stremio stream list (e.g. 'HDHub4u • 1080p WEB-DL')."""

    url: str
    """Direct video URL or M3U8 playlist URL."""

    behaviorHints: dict[str, Any] | None = None
    """Stremio behaviour hints (e.g. notWebReady, bingeGroup)."""

    description: str | None = None
    """Optional subtitle shown below the title in Stremio."""

    behavior_hints: dict | None = None
    """Stremio behaviour hints (e.g. notWebReady, bingeGroup)."""

    def to_stremio(self) -> dict:
        """Serialise to a Stremio-compatible stream object."""
        out: dict = {"title": self.title, "url": self.url}
        if self.description:
            out["description"] = self.description
        if self.behavior_hints:
            out["behaviorHints"] = self.behavior_hints
        return out


@dataclass
class ResolvedMeta:
    """Metadata resolved from an IMDb ID, used to search providers."""

    title: str
    """Canonical English title as returned by TMDB."""

    year: int | None
    """Release year. None when unavailable."""

    media_type: str
    """Either 'movie' or 'series'."""

    imdb_id: str
    """The original IMDb ID (e.g. 'tt1234567')."""

    season: int | None = None
    """Season number, populated for TV stream requests."""

    episode: int | None = None
    """Episode number, populated for TV stream requests."""


@dataclass
class ProviderResult:
    """Result bundle from a single provider search + extraction."""

    provider_name: str
    streams: list[StreamResult] = field(default_factory=list)
    error: str | None = None
