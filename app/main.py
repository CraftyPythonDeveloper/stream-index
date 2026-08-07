"""
stream-index — Stremio Addon

Entry point. Sets up:
  • Logging
  • python-stremio application with manifest
  • Stream handler that routes through provider pipeline

Design note on the HTTP client:
  httpx.AsyncClient must be bound to the running event loop.
  uvicorn creates its own event loop, so we cannot share a client
  that was created before uvicorn starts (e.g., in asyncio.run()).
  We create a single client lazily inside the blacksheep on_start hook,
  which runs inside uvicorn's loop, and hold it in a module-level variable
  for the lifetime of the process.
"""

from __future__ import annotations

import logging
import time

import httpx
from python_stremio import StremioApplication

from app.config import settings
from app.providers.hdhub4u import HDHub4uProvider
from app.services.metadata import resolve_imdb

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("stream-index")


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

MANIFEST: dict = {
    "id": settings.addon_id,
    "version": settings.addon_version,
    "name": settings.addon_name,
    "description": settings.addon_description,
    "logo": "",
    "resources": ["stream"],
    "types": ["movie", "series"],
    "idPrefixes": ["tt"],
    "catalogs": [],
    "behaviorHints": {
        "adult": False,
        "p2p": False,
    },
}


# ---------------------------------------------------------------------------
# Module-level HTTP client — initialised inside uvicorn's event loop
# ---------------------------------------------------------------------------

_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return the shared HTTP client. Must only be called after on_start."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout),
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
        )
    return _http_client


# ---------------------------------------------------------------------------
# Provider pipeline
# ---------------------------------------------------------------------------
# Adding another provider: instantiate it here and append to the list.

def _build_providers() -> list:
    client = _get_client()
    return [
        HDHub4uProvider(client),
    ]


# ---------------------------------------------------------------------------
# Stream request ID parser
# ---------------------------------------------------------------------------

def parse_stream_id(id_: str, type_: str) -> tuple[str, str, int | None, int | None]:
    """
    Parse Stremio stream request ID into components.

    Movies:  "tt1234567"
    Series:  "tt1234567:1:3"  (imdbId:season:episode)

    Returns (imdb_id, media_type, season, episode).
    """
    parts = id_.split(":")
    imdb_id = parts[0]
    season: int | None = None
    episode: int | None = None

    if type_ == "series" and len(parts) >= 3:
        try:
            season = int(parts[1])
            episode = int(parts[2])
        except ValueError:
            pass

    return imdb_id, type_, season, episode


# ---------------------------------------------------------------------------
# Build StremioApplication
# ---------------------------------------------------------------------------

app = StremioApplication(
    app_manifest=MANIFEST,
    bind_host=settings.addon_host,
    bind_port=settings.addon_port,
    public_url=settings.addon_public_url,
    server_log_level=settings.uvicorn_log_level,
)


# ---------------------------------------------------------------------------
# Startup hook — runs inside uvicorn's event loop (safe to create asyncio objects)
# ---------------------------------------------------------------------------

@app._web_app.on_start()
async def on_start() -> None:
    """Initialise the HTTP client and refresh the live HDHub4u domain."""
    client = _get_client()
    logger.info("HTTP client initialised inside uvicorn loop")
    await _refresh_hdhub4u_domain(client)


async def _refresh_hdhub4u_domain(client: httpx.AsyncClient) -> None:
    """Attempt to pull the current HDHub4u domain from the remote domains.json."""
    try:
        resp = await client.get(settings.hdhub4u_domains_url)
        resp.raise_for_status()
        data = resp.json()
        live_domain = data.get("HDHUB4u") or data.get("hdhub4u", "")
        if live_domain:
            settings.hdhub4u_base_url = live_domain.rstrip("/")
            logger.info("HDHub4u live domain: %s", settings.hdhub4u_base_url)
    except Exception:
        logger.warning(
            "Could not fetch live HDHub4u domain; using default: %s",
            settings.hdhub4u_base_url,
        )


# ---------------------------------------------------------------------------
# Stream handler
# ---------------------------------------------------------------------------

@app.on_stream()
async def handle_stream(req: dict) -> dict:
    """
    Main stream handler — called by python-stremio for every stream request.

    Flow:
      1. Parse the Stremio request id/type
      2. Resolve IMDb ID → canonical title via TMDB
      3. For each registered provider:
         a. Search for the title
         b. Extract streams from the best result
         c. Aggregate results
      4. Return {"streams": [...]}
    """
    id_ = req.get("id", "")
    type_ = req.get("type", "movie")
    t_total = time.monotonic()

    logger.info("Stream request — id=%s type=%s", id_, type_)

    imdb_id, media_type, season, episode = parse_stream_id(id_, type_)

    client = _get_client()

    # --- Metadata resolution ---
    meta = await resolve_imdb(client, imdb_id, media_type, season, episode)
    if meta is None:
        logger.warning("Metadata resolution failed for %s; returning empty", imdb_id)
        return {"streams": []}

    # --- Provider pipeline ---
    providers = _build_providers()
    all_streams: list[dict] = []

    for provider in providers:
        p_name = provider.name
        logger.info(
            "Trying provider: %s (title=%r season=%s ep=%s)",
            p_name, meta.title, season, episode,
        )

        try:
            t0 = time.monotonic()
            page_urls = await provider.search(meta)

            if not page_urls:
                logger.info("[%s] No search results found", p_name)
                continue

            # Use the top search result
            page_url = page_urls[0]
            logger.info("[%s] Using page: %s", p_name, page_url)

            streams = await provider.get_streams(page_url, meta)
            elapsed = time.monotonic() - t0

            logger.info(
                "[%s] Extracted %d stream(s) in %.2fs",
                p_name, len(streams), elapsed,
            )

            for s in streams:
                all_streams.append(s.to_stremio())

        except Exception:
            logger.exception("[%s] Unhandled exception during stream extraction", p_name)

    total_elapsed = time.monotonic() - t_total
    logger.info(
        "Stream request complete — %d stream(s) returned in %.2fs",
        len(all_streams), total_elapsed,
    )

    return {"streams": all_streams}


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------

@app.serve_landing_page()
async def landing(req) -> str:
    install_url = (
        f"stremio://{settings.addon_public_url}/manifest.json"
        if settings.addon_public_url
        else f"http://localhost:{settings.addon_port}/manifest.json"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>{settings.addon_name} — Stremio Addon</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #0f0f1a;
      color: #e0e0f0;
      font-family: 'Segoe UI', system-ui, sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 2rem;
    }}
    .card {{
      background: #1a1a2e;
      border: 1px solid #2d2d50;
      border-radius: 16px;
      padding: 3rem;
      max-width: 520px;
      width: 100%;
      text-align: center;
      box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    }}
    h1 {{ font-size: 2rem; margin-bottom: 0.5rem; color: #a78bfa; }}
    p {{ color: #9090b0; margin-bottom: 2rem; line-height: 1.6; }}
    .btn {{
      display: inline-block;
      background: #7c3aed;
      color: #fff;
      padding: 0.85rem 2rem;
      border-radius: 8px;
      text-decoration: none;
      font-weight: 600;
      transition: background 0.2s;
      margin: 0.4rem;
    }}
    .btn:hover {{ background: #6d28d9; }}
    .btn-secondary {{
      background: #1e1e3a;
      border: 1px solid #3d3d6a;
      color: #a0a0c0;
    }}
    .btn-secondary:hover {{ background: #2a2a4a; }}
    code {{
      display: block;
      margin-top: 1.5rem;
      padding: 0.75rem 1rem;
      background: #0d0d1a;
      border-radius: 6px;
      font-size: 0.85rem;
      color: #7dd3fc;
      word-break: break-all;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>🎬 {settings.addon_name}</h1>
    <p>{settings.addon_description}<br/>Powered by HDHub4u.</p>
    <a class="btn" href="{install_url}">Install in Stremio</a>
    <a class="btn btn-secondary" href="/manifest.json">View Manifest</a>
    <code>{install_url}</code>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point — start the addon server."""
    logger.info(
        "Starting %s v%s on %s:%d",
        settings.addon_name,
        settings.addon_version,
        settings.addon_host,
        settings.addon_port,
    )
    app.run()


if __name__ == "__main__":
    main()
