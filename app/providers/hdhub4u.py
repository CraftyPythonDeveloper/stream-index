"""
HDHub4u provider — searches HDHub4u and extracts playable streams.

Architecture
------------
search()
  → Pingora/Typesense search API → list[permalink URL]

get_streams(page_url, meta)
  → GET page → parse HTML
  → movie  : collect quality links → resolve redirect → extract_link()
  → tv     : parse episode blocks → resolve redirect → extract_link()

_resolve_redirect(url)
  → Decode the HDHub4u/WordPress redirect chain:
    1. Fetch redirect page HTML
    2. Extract base64 blobs via regex
    3. Concatenate → b64decode → ROT13 → b64decode → b64decode
    4. Parse JSON → decode 'o' field (encoded URL) or fallback GET
  → Returns final hosting-service URL (hubcloud, hubstream, pixeldrain…)

_extract_link(url, label)
  → Dispatch to the right extractor:
    hubcloud → _extract_hubcloud()
    pixeldrain → _extract_pixeldrain()
    hubstream / hblinks → parse page and re-dispatch
    otherwise → return as-is (direct link)
"""

from __future__ import annotations

import base64
import logging
import re
import time
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.models import ResolvedMeta, StreamResult
from app.providers.base import BaseProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants / regex
# ---------------------------------------------------------------------------

# Domains used to identify known hosting services
_HUBCLOUD_RE = re.compile(r"hubcloud", re.IGNORECASE)
_HUBSTREAM_RE = re.compile(r"hubstream", re.IGNORECASE)
_HBLINKS_RE = re.compile(r"hblinks", re.IGNORECASE)
_HUBDRIVE_RE = re.compile(r"hubdrive", re.IGNORECASE)
_HUBCDN_RE = re.compile(r"hubcdn", re.IGNORECASE)
_PIXELDRAIN_RE = re.compile(r"pixeldrain", re.IGNORECASE)

# Quality keywords used for stream labels
_QUALITY_RE = re.compile(r"(4K|2160p|1080p|720p|480p|UHD|WEB-?DL|WEBRip|BluRay|BDRip|HDRip)", re.IGNORECASE)
_EPISODE_RE = re.compile(r"EPiSODE\s*(\d+)", re.IGNORECASE)
_QUALITY_LINK_RE = re.compile(r"(480|720|1080|2160|4K)", re.IGNORECASE)

# Redirect page extraction regex (matches both known patterns on HDHub4u WP pages)
_REDIRECT_BLOB_RE = re.compile(
    r"""s\('o','([A-Za-z0-9+/=]+)'|ck\('_wp_http_\d+','([^']+)'""",
)

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class HDHub4uProvider(BaseProvider):
    """Searches and extracts streams from HDHub4u."""

    name = "HDHub4u"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._headers = {
            "User-Agent": settings.user_agent,
            "Cookie": "xla=s4t",
        }

    @property
    def _base_url(self) -> str:
        """Read the live domain from settings (updated by startup hook)."""
        return settings.hdhub4u_base_url

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def search(self, meta: ResolvedMeta) -> list[str]:
        """
        Search HDHub4u via the Pingora/Typesense API.

        Returns a list of permalink URLs ranked by relevance.
        At most 5 results are returned to keep extraction fast.
        """
        query = meta.title
        if meta.year:
            query = f"{meta.title} {meta.year}"

        logger.info("[HDHub4u] Searching: %r", query)
        t0 = time.monotonic()

        try:
            resp = await self._client.get(
                settings.hdhub4u_search_url,
                params={
                    "q": query,
                    "query_by": "post_title,category",
                    "query_by_weights": "4,2",
                    "sort_by": "sort_by_date:desc",
                    "limit": 10,
                    "highlight_fields": "none",
                    "use_cache": "true",
                    "page": 1,
                },
                headers={**self._headers, "Referer": self._base_url, "Origin": self._base_url},
            )
            resp.raise_for_status()
        except Exception:
            logger.exception("[HDHub4u] Search request failed for %r", query)
            return []

        elapsed = time.monotonic() - t0
        body = resp.json()
        hits = body.get("hits", [])
        urls: list[str] = []

        for hit in hits[:5]:
            doc = hit.get("document", {})
            permalink = doc.get("permalink", "")
            post_title = doc.get("post_title", "")
            if permalink:
                # Pingora may return relative paths — make them absolute
                if not permalink.startswith("http"):
                    permalink = self._base_url.rstrip("/") + "/" + permalink.lstrip("/")
                urls.append(permalink)
                logger.debug("[HDHub4u] Hit: %r → %s", post_title, permalink)

        logger.info("[HDHub4u] Search returned %d result(s) in %.2fs", len(urls), elapsed)
        return urls

    async def get_streams(
        self,
        page_url: str,
        meta: ResolvedMeta,
    ) -> list[StreamResult]:
        """
        Fetch the HDHub4u content page and extract streams.

        Dispatches to movie or TV episode extraction based on meta.media_type.
        """
        logger.info("[HDHub4u] Fetching page: %s", page_url)
        t0 = time.monotonic()

        try:
            resp = await self._client.get(page_url, headers=self._headers, follow_redirects=True)
            resp.raise_for_status()
        except Exception:
            logger.exception("[HDHub4u] Failed to fetch page: %s", page_url)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        elapsed = time.monotonic() - t0
        logger.debug("[HDHub4u] Page fetched in %.2fs", elapsed)

        if meta.media_type == "movie":
            return await self._extract_movie_streams(soup, page_url)
        else:
            return await self._extract_episode_streams(soup, page_url, meta)

    # ------------------------------------------------------------------
    # Movie extraction
    # ------------------------------------------------------------------

    async def _extract_movie_streams(
        self,
        soup: BeautifulSoup,
        page_url: str,
    ) -> list[StreamResult]:
        """Parse movie page and extract quality links."""
        raw_links: list[str] = []

        # Quality-labelled links under h3/h4 headings
        for tag in soup.select("h3 a, h4 a"):
            href = tag.get("href", "")
            text = tag.get_text()
            if href and _QUALITY_LINK_RE.search(text):
                raw_links.append(href)

        # Broad link sweep in page body for known hosting domains
        for tag in soup.select(".page-body > div a, .entry-content a"):
            href = tag.get("href", "")
            if href and self._is_hosting_link(href):
                raw_links.append(href)

        # Deduplicate preserving order
        seen: set[str] = set()
        unique_links = [u for u in raw_links if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]

        logger.info("[HDHub4u] Movie: found %d raw link(s)", len(unique_links))

        streams: list[StreamResult] = []
        for link in unique_links:
            extracted = await self._process_link(link, source_label="HDHub4u")
            streams.extend(extracted)
            if len(streams) >= 8:
                break

        return streams

    # ------------------------------------------------------------------
    # TV episode extraction
    # ------------------------------------------------------------------

    async def _extract_episode_streams(
        self,
        soup: BeautifulSoup,
        page_url: str,
        meta: ResolvedMeta,
    ) -> list[StreamResult]:
        """
        Parse a TV season page and extract links for the requested episode.

        Two patterns exist on HDHub4u:

        Pattern A — Quality blocks (h3 with 1080/720/4K text):
          Each h3 has links → resolve redirect → fetch episode-index page
          → parse <h5 a> per-episode links → find target episode

        Pattern B — Episode blocks (h4 with "EPiSODE N" text):
          Collect all <a href> siblings until <hr> separator
        """
        target_ep = meta.episode

        # Build per-episode link map: {episode_num: [urls]}
        ep_links: dict[int, list[str]] = {}

        for heading in soup.select("h3, h4"):
            text = heading.get_text()
            ep_match = _EPISODE_RE.search(text)
            is_quality_block = bool(_QUALITY_LINK_RE.search(text))

            if is_quality_block and heading.name == "h3":
                # Pattern A — need to resolve redirect and fetch episode index
                base_links = [a.get("href", "") for a in heading.select("a[href]") if a.get("href")]
                for link in base_links:
                    if not link:
                        continue
                    try:
                        resolved = await self._resolve_redirect(link.strip())
                        if not resolved:
                            continue
                        index_resp = await self._client.get(
                            resolved, headers=self._headers, follow_redirects=True
                        )
                        index_soup = BeautifulSoup(index_resp.text, "lxml")
                        for ep_link in index_soup.select("h5 a"):
                            ep_text = ep_link.get_text()
                            ep_href = ep_link.get("href", "")
                            ep_num_match = re.search(r"Episode\s*(\d+)", ep_text, re.IGNORECASE)
                            if ep_num_match and ep_href:
                                ep_num = int(ep_num_match.group(1))
                                ep_links.setdefault(ep_num, []).append(ep_href)
                    except Exception:
                        logger.debug("[HDHub4u] Pattern A: failed to resolve %s", link, exc_info=True)

            elif ep_match and not is_quality_block:
                # Pattern B — direct episode links in siblings
                ep_num = int(ep_match.group(1))
                all_ep_links: list[str] = []

                # Links in the heading itself
                all_ep_links.extend(
                    a.get("href", "") for a in heading.select("a[href]") if a.get("href")
                )

                # Links in siblings until next <hr>
                if heading.name == "h4":
                    sibling = heading.find_next_sibling()
                    while sibling and sibling.name != "hr":
                        for a in sibling.find_all("a", href=True):
                            all_ep_links.append(a["href"])
                        sibling = sibling.find_next_sibling()

                if all_ep_links:
                    ep_links.setdefault(ep_num, []).extend(all_ep_links)

        if target_ep is None:
            logger.warning("[HDHub4u] No episode number in request; returning empty")
            return []

        links_for_ep = ep_links.get(target_ep, [])
        if not links_for_ep:
            logger.warning(
                "[HDHub4u] Episode %d not found in page; available: %s",
                target_ep,
                sorted(ep_links.keys()),
            )
            return []

        logger.info("[HDHub4u] Episode %d: %d link(s) found", target_ep, len(links_for_ep))

        streams: list[StreamResult] = []
        for link in links_for_ep:
            extracted = await self._process_link(link, source_label=f"HDHub4u E{target_ep:02d}")
            streams.extend(extracted)
            if len(streams) >= 8:
                break

        return streams

    # ------------------------------------------------------------------
    # Link processing pipeline
    # ------------------------------------------------------------------

    async def _process_link(self, raw_url: str, source_label: str) -> list[StreamResult]:
        """
        Take a raw link from the page and produce zero or more StreamResults.

        Steps:
          1. If the link is a redirect (?id= present), resolve it.
          2. Dispatch to the appropriate extractor based on domain.
        """
        url = raw_url.strip()
        if not url.startswith("http"):
            return []

        # Resolve redirect if needed
        if "?id=" in url or self._is_redirect_link(url):
            try:
                url = await self._resolve_redirect(url) or url
            except Exception:
                logger.debug("[HDHub4u] Redirect resolution failed: %s", raw_url, exc_info=True)

        if not url.startswith("http"):
            return []

        return await self._extract_link(url, source_label)

    async def _extract_link(self, url: str, label: str) -> list[StreamResult]:
        """Dispatch a resolved URL to the correct extractor."""
        try:
            if _HUBCLOUD_RE.search(url):
                return await self._extract_hubcloud(url, label)
            elif _PIXELDRAIN_RE.search(url):
                return self._extract_pixeldrain(url, label)
            elif _HUBDRIVE_RE.search(url):
                return await self._extract_hubdrive(url, label)
            elif _HBLINKS_RE.search(url) or _HUBSTREAM_RE.search(url):
                return await self._extract_hblinks(url, label)
            elif _HUBCDN_RE.search(url):
                return await self._extract_hubcdn(url, label)
            else:
                # Unknown / direct link — return as-is
                quality = self._detect_quality(url)
                title = f"{label} • {quality}" if quality else label
                return [StreamResult(title=title, url=url, behavior_hints={"notWebReady": True})]
        except Exception:
            logger.warning("[HDHub4u] Extractor failed for %s", url, exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Redirect chain decoder
    # ------------------------------------------------------------------

    async def _resolve_redirect(self, url: str) -> str | None:
        """
        Decode the HDHub4u WordPress redirect chain.

        The WordPress page hides the destination URL in obfuscated JS blobs:
          base64 → ROT13 → base64 → base64 → JSON with keys 'o', 'data', 'blog_url'

        'o' is the encoded final URL; fallback is a GET to blog_url?re=<data>.
        """
        try:
            resp = await self._client.get(url, headers=self._headers, follow_redirects=True)
            html = resp.text
        except Exception:
            logger.debug("[HDHub4u] Could not fetch redirect page: %s", url)
            return None

        matches = _REDIRECT_BLOB_RE.findall(html)
        if not matches:
            logger.debug("[HDHub4u] No redirect blobs found in %s", url)
            return None

        combined = "".join(
            group1 or group2 for group1, group2 in matches if group1 or group2
        )
        if not combined:
            return None

        try:
            step1 = _b64decode(combined)
            step2 = _b64decode(step1)
            step3 = _rot13(step2)
            step4 = _b64decode(step3)
        except Exception:
            logger.debug("[HDHub4u] Redirect decode chain failed for %s", url, exc_info=True)
            return None

        import json as _json_mod
        try:
            payload = _json_mod.loads(step4)
        except Exception:
            logger.debug("[HDHub4u] Redirect JSON parse failed: %r", step4[:200])
            return None

        encoded_url = payload.get("o", "")
        if encoded_url:
            try:
                return _b64decode(encoded_url).strip()
            except Exception:
                pass

        # Fallback: hit the blog URL
        blog_url = payload.get("blog_url", "").strip()
        data_field = payload.get("data", "").strip()
        if blog_url and data_field:
            try:
                decoded_data = _b64decode(data_field).strip()
                fallback_resp = await self._client.get(
                    f"{blog_url}?re={decoded_data}",
                    headers=self._headers,
                    follow_redirects=True,
                )
                body_text = BeautifulSoup(fallback_resp.text, "lxml").get_text().strip()
                if body_text.startswith("http"):
                    return body_text
            except Exception:
                logger.debug("[HDHub4u] Redirect fallback GET failed", exc_info=True)

        return None

    # ------------------------------------------------------------------
    # Hosting service extractors
    # ------------------------------------------------------------------

    async def _extract_hubcloud(self, url: str, label: str) -> list[StreamResult]:
        """
        HubCloud download page extractor.

        Flow:
          1. Optionally GET the entry page to find the #download link.
          2. GET the download page.
          3. Parse <a.btn> elements to find FSL Server / BuzzServer / Pixeldrain links.
        """
        streams: list[StreamResult] = []
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        try:
            if "hubcloud.php" in url:
                dl_url = url
            else:
                entry_resp = await self._client.get(
                    url, headers=self._headers, follow_redirects=True
                )
                entry_soup = BeautifulSoup(entry_resp.text, "lxml")
                raw_href = (
                    entry_soup.select_one("#download") or
                    entry_soup.select_one("a[href*='hubcloud']")
                )
                if raw_href:
                    href = raw_href.get("href", "")
                    dl_url = href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")
                else:
                    dl_url = url

            dl_resp = await self._client.get(dl_url, headers=self._headers, follow_redirects=True)
            dl_soup = BeautifulSoup(dl_resp.text, "lxml")

            header_text = (dl_soup.select_one("div.card-header") or dl_soup.select_one("h4")).get_text() if (dl_soup.select_one("div.card-header") or dl_soup.select_one("h4")) else ""
            quality = self._detect_quality(header_text) or self._detect_quality(url) or "Unknown"
            size_el = dl_soup.select_one("i#size")
            size = size_el.get_text().strip() if size_el else ""
            size_label = f" [{size}]" if size else ""

            for btn in dl_soup.select("a.btn"):
                link = btn.get("href", "")
                btn_text = btn.get_text().strip().lower()

                if not link:
                    continue

                if "fsl server" in btn_text:
                    streams.append(StreamResult(
                        title=f"{label} • {quality}{size_label} [FSL]",
                        url=link,
                        behavior_hints={"notWebReady": True}
                    ))
                elif "download file" in btn_text or "direct" in btn_text:
                    streams.append(StreamResult(
                        title=f"{label} • {quality}{size_label} [Direct]",
                        url=link,
                        behavior_hints={"notWebReady": True}
                    ))
                elif "buzzserver" in btn_text:
                    try:
                        buzz_resp = await self._client.get(
                            f"{link}/download",
                            headers=self._headers,
                            follow_redirects=False,
                        )
                        redirect_url = (
                            buzz_resp.headers.get("hx-redirect")
                            or buzz_resp.headers.get("HX-Redirect")
                            or buzz_resp.headers.get("location")
                            or ""
                        )
                        if redirect_url:
                            streams.append(StreamResult(
                                title=f"{label} • {quality}{size_label} [BuzzServer]",
                                url=redirect_url,
                                behavior_hints={"notWebReady": True}
                            ))
                    except Exception:
                        logger.debug("[HDHub4u] BuzzServer redirect failed for %s", link)
                elif "pixeldra" in btn_text or "pixeldrain" in btn_text:
                    pd_url = self._build_pixeldrain_url(link)
                    streams.append(StreamResult(
                        title=f"{label} • {quality}{size_label} [Pixeldrain]",
                        url=pd_url,
                        behavior_hints={"notWebReady": True}
                    ))
                elif "s3 server" in btn_text:
                    streams.append(StreamResult(
                        title=f"{label} • {quality}{size_label} [S3]",
                        url=link,
                        behavior_hints={"notWebReady": True}
                    ))
                elif "fslv2" in btn_text:
                    streams.append(StreamResult(
                        title=f"{label} • {quality}{size_label} [FSLv2]",
                        url=link,
                        behavior_hints={"notWebReady": True}
                    ))
                elif "mega server" in btn_text:
                    streams.append(StreamResult(
                        title=f"{label} • {quality}{size_label} [Mega]",
                        url=link,
                        behavior_hints={"notWebReady": True}
                    ))

        except Exception:
            logger.warning("[HDHub4u] HubCloud extraction failed for %s", url, exc_info=True)

        return streams

    def _extract_pixeldrain(self, url: str, label: str) -> list[StreamResult]:
        """Convert any Pixeldrain page URL to a direct API download URL."""
        pd_url = self._build_pixeldrain_url(url)
        quality = self._detect_quality(url) or "Unknown"
        return [StreamResult(title=f"{label} • {quality} [Pixeldrain]", url=pd_url, behavior_hints={"notWebReady": True})]

    async def _extract_hubdrive(self, url: str, label: str) -> list[StreamResult]:
        """HubDrive → resolves to HubCloud or direct link."""
        try:
            resp = await self._client.get(url, headers=self._headers, follow_redirects=True)
            soup = BeautifulSoup(resp.text, "lxml")
            btn = soup.select_one(".btn.btn-primary, .btn-success1")
            href = btn.get("href", "") if btn else ""
            if not href:
                return []
            if _HUBCLOUD_RE.search(href):
                return await self._extract_hubcloud(href, label)
            quality = self._detect_quality(href) or "Unknown"
            return [StreamResult(title=f"{label} • {quality}", url=href, behavior_hints={"notWebReady": True})]
        except Exception:
            logger.debug("[HDHub4u] HubDrive extraction failed for %s", url, exc_info=True)
            return []

    async def _extract_hblinks(self, url: str, label: str) -> list[StreamResult]:
        """Hblinks/Hubstream — parse inner page and dispatch nested links."""
        streams: list[StreamResult] = []
        try:
            resp = await self._client.get(url, headers=self._headers, follow_redirects=True)
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a"):
                href = a.get("href", "")
                if href and href.startswith("http") and self._is_hosting_link(href):
                    nested = await self._extract_link(href, label)
                    streams.extend(nested)
        except Exception:
            logger.debug("[HDHub4u] Hblinks extraction failed for %s", url, exc_info=True)
        return streams

    async def _extract_hubcdn(self, url: str, label: str) -> list[StreamResult]:
        """HubCDN — base64 encoded URL in a script tag."""
        try:
            resp = await self._client.get(url, headers=self._headers, follow_redirects=True)
            script = ""
            for s in resp.text.split("<script"):
                if "reurl" in s:
                    script = s
                    break
            match = re.search(r'reurl\s*=\s*"([^"]+)"', script)
            if not match:
                return []
            encoded = match.group(1).split("?r=")[-1]
            decoded = _b64decode(encoded)
            final_url = decoded.split("link=")[-1].strip()
            quality = self._detect_quality(final_url) or "Unknown"
            return [StreamResult(title=f"{label} • {quality} [HubCDN]", url=final_url, behavior_hints={"notWebReady": True})]
        except Exception:
            logger.debug("[HDHub4u] HubCDN extraction failed for %s", url, exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    @staticmethod
    def _is_hosting_link(url: str) -> bool:
        """Return True if the URL points to a known hosting service."""
        patterns = (
            "hdstream4u", "hubstream", "hubcloud", "hubdrive",
            "hubcdn", "pixeldrain", "hblinks",
        )
        lower = url.lower()
        return any(p in lower for p in patterns)

    @staticmethod
    def _is_redirect_link(url: str) -> bool:
        """Return True if the URL is a WordPress redirect page."""
        return "?id=" in url or "/go/" in url or "redirect" in url.lower()

    @staticmethod
    def _detect_quality(text: str) -> str | None:
        """Extract the best quality label from a URL or text string."""
        m = _QUALITY_RE.search(text)
        return m.group(0) if m else None

    @staticmethod
    def _build_pixeldrain_url(url: str) -> str:
        """Build a direct Pixeldrain API download URL from any Pixeldrain link."""
        if "/api/file/" in url and "download" in url:
            return url
        file_id = url.rstrip("/").split("/")[-1]
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        return f"{base}/api/file/{file_id}?download"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _b64decode(data: str) -> str:
    """Base64 decode with automatic padding fix. Returns UTF-8 string."""
    data = re.sub(r"[^A-Za-z0-9+/=]", "", data)
    padded = data + "=" * (-len(data) % 4)
    return base64.b64decode(padded).decode("utf-8", errors="replace")


def _rot13(text: str) -> str:
    """Apply ROT13 transformation (letters only)."""
    result = []
    for ch in text:
        if "A" <= ch <= "Z":
            result.append(chr((ord(ch) - ord("A") + 13) % 26 + ord("A")))
        elif "a" <= ch <= "z":
            result.append(chr((ord(ch) - ord("a") + 13) % 26 + ord("a")))
        else:
            result.append(ch)
    return "".join(result)
