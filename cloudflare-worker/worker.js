/**
 * stream-index — Redirect Resolver Worker
 *
 * Endpoint:  GET /resolve?url=<base64url-encoded-url>&token=<secret>
 *
 * What it does
 * ------------
 * 1. Decodes the target URL from the `url` query parameter (base64url, no padding).
 * 2. Follows the full redirect chain for that URL server-side (HEAD first,
 *    GET fallback) using the required auth headers (Cookie: xla=s4t, etc.).
 * 3. Returns HTTP 302 → final CDN URL.
 *
 * Why this fixes seeking
 * ---------------------
 * Players (ExoPlayer, VLC, mpv) seek by opening a NEW connection with a
 * Range: bytes=<offset>- header.  The redirect chain from hosting services
 * (FSL, BuzzServer, HubCloud…) drops the Range header before it reaches the
 * CDN, so the CDN always responds 200 from byte 0 → player resets to start.
 *
 * By resolving the redirect chain here and handing the player a bare CDN URL,
 * the player's Range request goes DIRECTLY to the CDN which does support 206.
 *
 * Zero video bytes pass through this Worker — it only returns tiny redirects.
 * This keeps it within Cloudflare's free-tier Terms of Service.
 *
 * Environment secrets (set via wrangler secret or the CF dashboard)
 * -----------------------------------------------------------------
 * PROXY_TOKEN  — optional shared secret.  When set, every /resolve request
 *                must include ?token=<value> or it gets a 401.
 */

const UPSTREAM_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
  // Required by HDHub4u / HubCloud to unlock download pages
  Cookie: "xla=s4t",
};

/** Maximum number of manual redirect hops before giving up. */
const MAX_HOPS = 12;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // ── CORS pre-flight ────────────────────────────────────────────────────
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders(),
      });
    }

    // ── Route guard ────────────────────────────────────────────────────────
    if (url.pathname !== "/resolve") {
      return new Response("Not found", { status: 404 });
    }

    // ── Token auth ─────────────────────────────────────────────────────────
    const token = url.searchParams.get("token") ?? "";
    if (env.PROXY_TOKEN && token !== env.PROXY_TOKEN) {
      return new Response("Unauthorized", {
        status: 401,
        headers: corsHeaders(),
      });
    }

    // ── Decode target URL ──────────────────────────────────────────────────
    const encodedUrl = url.searchParams.get("url");
    if (!encodedUrl) {
      return new Response("Missing required parameter: url", {
        status: 400,
        headers: corsHeaders(),
      });
    }

    let targetUrl;
    try {
      // base64url → base64 standard, then decode
      const b64 = encodedUrl.replace(/-/g, "+").replace(/_/g, "/");
      // atob needs proper padding
      const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
      targetUrl = atob(padded);
    } catch {
      return new Response("Invalid base64url encoding for url parameter", {
        status: 400,
        headers: corsHeaders(),
      });
    }

    if (!targetUrl.startsWith("http")) {
      return new Response("Target URL must start with http(s)://", {
        status: 400,
        headers: corsHeaders(),
      });
    }

    // ── Resolve redirect chain ─────────────────────────────────────────────
    const finalUrl = await resolveRedirects(targetUrl);
    if (!finalUrl) {
      return new Response("Failed to resolve redirect chain", {
        status: 502,
        headers: corsHeaders(),
      });
    }

    // ── Return 302 to the final CDN URL ────────────────────────────────────
    return new Response(null, {
      status: 302,
      headers: {
        Location: finalUrl,
        "Cache-Control": "no-store",
        ...corsHeaders(),
      },
    });
  },
};

/**
 * Follow the redirect chain for `startUrl` and return the final URL.
 *
 * Strategy:
 *   1. Try a HEAD request with redirect:'follow' — fast, no body download.
 *   2. If HEAD fails (405 Method Not Allowed or network error), fall back to
 *      manual GET-based redirect following (never downloads the body).
 *
 * @param {string} startUrl
 * @returns {Promise<string|null>} Final URL, or null on error.
 */
async function resolveRedirects(startUrl) {
  // ── Attempt 1: HEAD + follow ───────────────────────────────────────────
  try {
    const resp = await fetch(startUrl, {
      method: "HEAD",
      headers: UPSTREAM_HEADERS,
      redirect: "follow",
    });
    // resp.url is the final URL after all redirects
    if (resp.url && resp.url.startsWith("http")) {
      return resp.url;
    }
  } catch {
    // HEAD failed — fall through to manual GET approach
  }

  // ── Attempt 2: Manual GET redirect following (no body consumed) ────────
  try {
    let currentUrl = startUrl;
    for (let hop = 0; hop < MAX_HOPS; hop++) {
      const resp = await fetch(currentUrl, {
        method: "GET",
        headers: UPSTREAM_HEADERS,
        redirect: "manual", // do NOT auto-follow so we can inspect Location
      });

      if (resp.status >= 300 && resp.status < 400) {
        const location = resp.headers.get("Location");
        if (!location) break;
        // Resolve relative locations
        currentUrl = location.startsWith("http")
          ? location
          : new URL(location, currentUrl).href;
      } else {
        // Reached the end of the redirect chain
        return currentUrl;
      }
    }
    return currentUrl;
  } catch {
    return null;
  }
}

/** Standard CORS headers so Stremio web app can also use this endpoint. */
function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Range, Content-Type",
  };
}
