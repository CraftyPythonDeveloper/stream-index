# stream-index Redirect Resolver — Cloudflare Worker

A tiny Cloudflare Worker that sits between your Stremio addon and the player.
It resolves the redirect chains produced by hosting services (FSL, BuzzServer,
HubCloud, etc.) **server-side** and returns a bare `302` pointing directly at
the final CDN URL.

This lets video players issue `Range:` requests straight to the CDN, fixing the
seek-resets-to-start problem without routing any video bytes through the Worker.

---

## Why it works

```
Without Worker (broken seeking)
  Player → GET HubCloud/FSL URL (no Range support or drops Range in redirect)
          ← 200 OK from byte 0  →  seek resets

With Worker (seeking works)
  Player → GET /resolve?url=<encoded> (Range: bytes=N-)
  Worker → resolves full redirect chain server-side
          → 302 Location: cdn.example.com/file.mp4?sig=...
  Player → GET cdn.example.com/file.mp4 (Range: bytes=N-)
          ← 206 Partial Content  →  seek works ✓
```

Zero video bytes pass through the Worker — it only serves tiny HTTP redirects.

---

## Prerequisites

- A **free Cloudflare account** — [sign up](https://dash.cloudflare.com/sign-up)
- **Node.js 18+** installed locally
- **Wrangler CLI**:

```bash
npm install -g wrangler
```

---

## Deploy

### 1. Log in to Cloudflare

```bash
wrangler login
```

This opens a browser window. Authorise Wrangler with your account.

### 2. Deploy the Worker

From the `cloudflare-worker/` directory:

```bash
wrangler deploy
```

Wrangler prints the Worker URL when done, e.g.:

```
https://stream-index-resolver.<your-subdomain>.workers.dev
```

### 3. Set the secret token (recommended)

The `PROXY_TOKEN` secret prevents strangers from using your Worker as an open
proxy. Pick any random string:

```bash
wrangler secret put PROXY_TOKEN
# Paste your secret when prompted, e.g.: my-super-secret-42
```

### 4. Configure the addon

In your `.env` file (copy `.env.example` if you haven't already):

```env
CF_WORKER_URL=https://stream-index-resolver.<your-subdomain>.workers.dev
CF_WORKER_TOKEN=my-super-secret-42
```

Restart the addon. All stream URLs (except Pixeldrain, which supports Range
natively) will be routed through the Worker resolver.

---

## Verify it works

Open the Worker URL in a browser:

```
https://stream-index-resolver.<your-subdomain>.workers.dev/resolve
```

You should see: `Missing required parameter: url` — that confirms it's live.

To test end-to-end, play a movie in Stremio and try seeking. The video should
resume from the seeked position without resetting to the start.

You can also watch live requests in the Cloudflare dashboard under
**Workers & Pages → stream-index-resolver → Logs**.

---

## Free-tier limits

| Metric | Free tier | Typical usage |
|---|---|---|
| Requests / day | 100,000 | ~50–200 per movie (1 per seek) |
| CPU time / request | 10 ms | < 1 ms (only redirect logic) |
| Egress bandwidth | N/A | 0 (no video bytes) |

The free tier is more than sufficient for personal use.

---

## Update / redeploy

```bash
wrangler deploy
```

---

## Remove / undeploy

```bash
wrangler delete
```
