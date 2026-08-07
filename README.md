# Stream Index — Stremio Addon

A **production-ready Stremio addon** written in Python that searches **HDHub4u** and returns playable streams — including for Android TV.

Built with [`python-stremio`](https://pypi.org/project/python-stremio/), `httpx`, and `BeautifulSoup4`.

---

## Features

- 🎬 **Movies** — search and stream movies from HDHub4u
- 📺 **TV Shows** — per-episode stream extraction from season pages
- 🔗 **Smart redirect chain** — decodes HDHub4u's ROT13 + base64 obfuscation
- ☁️ **HubCloud extractor** — FSL Server, BuzzServer, Pixeldrain, S3, direct
- 🌐 **Live domain refresh** — fetches the current HDHub4u domain at startup
- 🔒 **TMDB metadata** — resolves IMDb IDs to canonical search titles
- ⚡ **Fully async** — `httpx` + `asyncio` throughout
- 📋 **Structured logging** — every request step is logged for easy debugging

---

## Requirements

- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Python 3.14+ (installed automatically by uv)

---

## Quick Start

### 1. Clone / Download

```bash
git clone <your-repo-url>
cd stream-index
```

### 2. Configure (optional)

```bash
cp .env.example .env
# Edit .env with your settings
```

The defaults work out of the box. The most useful setting for Android TV is `ADDON_PUBLIC_URL`:

```env
# Set to your LAN IP so Android TV can reach the addon
ADDON_PUBLIC_URL=http://192.168.1.100:7000
```

### 3. Install & Run

```bash
uv sync
uv run python -m app.main
```

The addon starts at `http://0.0.0.0:7000`.

---

## Installing in Stremio

### Desktop / Web

Open the landing page and click **Install in Stremio**:

```
http://localhost:7000
```

Or add the manifest URL directly in Stremio → Add Addon:

```
http://localhost:7000/manifest.json
```

### Android TV

1. Make sure your PC and Android TV are on the same network.
2. Set `ADDON_PUBLIC_URL=http://<your-pc-ip>:7000` in `.env`.
3. Restart the addon.
4. In Stremio on Android TV, go to **Addons → Community Addons** and enter:
   ```
   http://<your-pc-ip>:7000/manifest.json
   ```

---

## Configuration

All settings are configurable via environment variables or a `.env` file. See [`.env.example`](.env.example) for the full list.

| Variable | Default | Description |
|---|---|---|
| `ADDON_HOST` | `0.0.0.0` | Bind address |
| `ADDON_PORT` | `7000` | Listen port |
| `ADDON_PUBLIC_URL` | _(empty)_ | Public URL for installation link |
| `LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `REQUEST_TIMEOUT` | `20.0` | HTTP timeout in seconds |
| `HDHUB4U_BASE_URL` | _(auto)_ | Override HDHub4u domain (auto-fetched at startup) |
| `TMDB_API_KEY` | _(included)_ | TMDB API key for metadata resolution |
| `TMDB_BASE_URL` | CF Workers proxy | TMDB base URL (swap to `https://api.themoviedb.org/3` for your own key) |

---

## Running Tests

```bash
uv run pytest tests/ -v
```

All HTTP calls in tests are mocked with `respx` — no real network needed.

---

## Project Structure

```
stream-index/
├── app/
│   ├── main.py             # Addon entry point + stream handler
│   ├── config.py           # Environment-driven configuration
│   ├── models.py           # Typed dataclasses (StreamResult, ResolvedMeta)
│   ├── providers/
│   │   ├── base.py         # Abstract provider interface
│   │   └── hdhub4u.py      # HDHub4u search + stream extraction
│   └── services/
│       └── metadata.py     # IMDb → title resolution via TMDB
├── tests/
│   ├── test_metadata.py    # Mocked TMDB tests
│   └── test_hdhub4u.py     # Mocked provider + utility tests
├── pyproject.toml
├── .env.example
└── README.md
```

---

## How It Works

```
Stremio (IMDb ID)
    │
    ▼
TMDB /find/{imdbId}
    │  → canonical title + year
    ▼
Pingora/Typesense search API
    │  → HDHub4u post permalink
    ▼
GET permalink → parse HTML
    │  movies: h3/h4 quality links
    │  TV: episode blocks → per-episode links
    ▼
Redirect chain decode
    │  base64 → ROT13 → base64 → base64 → JSON → final hosting URL
    ▼
Extractor dispatch
    │  HubCloud → FSL/BuzzServer/Pixeldrain/S3
    │  Pixeldrain → direct API URL
    │  Hubdrive → HubCloud
    │  HubCDN → base64 decode
    ▼
Stremio streams []
```

---

## Adding a New Provider

1. Create `app/providers/myprovider.py` implementing `BaseProvider`:
   ```python
   class MyProvider(BaseProvider):
       name = "MyProvider"
       async def search(self, meta: ResolvedMeta) -> list[str]: ...
       async def get_streams(self, page_url: str, meta: ResolvedMeta) -> list[StreamResult]: ...
   ```
2. Register in `app/main.py`:
   ```python
   def _build_providers() -> list:
       return [HDHub4uProvider(client), MyProvider(client)]
   ```

That's it. The stream handler automatically tries all providers.

---

## Troubleshooting

### No streams returned

1. Check logs for the specific failure stage:
   - **TMDB failed** → Check `TMDB_API_KEY` and `TMDB_BASE_URL`
   - **Search 403** → The Pingora API may have changed its CORS policy
   - **Page fetch failed** → HDHub4u domain may have rotated; check `HDHUB4U_BASE_URL`
   - **Redirect decode failed** → HDHub4u obfuscation may have changed

2. Set `LOG_LEVEL=DEBUG` for verbose output.

3. Verify the live domain is being picked up at startup:
   ```
   INFO  stream-index  HDHub4u live domain: https://new4.hdhub4u.cl
   ```

### Android TV can't reach addon

- Ensure `ADDON_PUBLIC_URL` is set to your PC's LAN IP.
- Confirm port 7000 is not blocked by Windows Firewall.
- Try: `http://<your-pc-ip>:7000/manifest.json` in a browser on the same network.

### Domain rotated

HDHub4u changes its domain occasionally. The addon auto-fetches the live domain from:
```
https://raw.githubusercontent.com/phisher98/TVVVV/refs/heads/main/domains.json
```

If this URL is stale, set `HDHUB4U_BASE_URL=https://<new-domain>` in `.env`.

---

## Future Enhancements

The following are **intentionally out of scope** for this MVP:

- MultiMovies provider
- Additional providers (FourKHDHub, Filmyfiy, etc.)
- Response caching (Redis)
- Proxy rotation
- CAPTCHA solving
- Provider health monitoring
- Distributed scraping

---

## License

MIT
