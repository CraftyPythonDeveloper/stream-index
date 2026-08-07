"""
Abstract base class that every provider must implement.

Adding a new provider requires:
1. Create a new file in app/providers/
2. Subclass BaseProvider and implement search() + get_streams()
3. Register it in app/main.py's PROVIDERS list
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import ResolvedMeta, StreamResult


class BaseProvider(ABC):
    """Contract that every streaming provider must satisfy."""

    name: str = "unnamed"

    @abstractmethod
    async def search(self, meta: ResolvedMeta) -> list[str]:
        """
        Search the provider for content matching *meta*.

        Returns a list of content-page URLs (e.g. post permalink on HDHub4u).
        Returns an empty list when nothing is found.
        """

    @abstractmethod
    async def get_streams(
        self,
        page_url: str,
        meta: ResolvedMeta,
    ) -> list[StreamResult]:
        """
        Extract playable streams from a content page URL.

        *meta* carries season/episode numbers for TV requests.
        Returns an empty list when extraction fails.
        """
