/**
 * Cloudflare Worker: Zero-Downtime Reverse Proxy for render-pool
 *
 * Proxies traffic from api.yourdomain.com -> Active Render instance.
 * When render-pool switches shifts, it updates ACTIVE_BACKEND_URL in KV or Secret,
 * instantly rerouting all frontend traffic without DNS propagation delay.
 */

export default {
  async fetch(request, env, ctx) {
    // 1. Resolve active backend URL (KV namespace takes precedence, then environment secret)
    let backendUrl = null;
    if (env.ROUTER_KV) {
      backendUrl = await env.ROUTER_KV.get("ACTIVE_BACKEND_URL");
    }
    if (!backendUrl && env.ACTIVE_BACKEND_URL) {
      backendUrl = env.ACTIVE_BACKEND_URL;
    }

    if (!backendUrl) {
      return new Response(
        JSON.stringify({
          error: "No active Render backend configured in router.",
          hint: "Run 'render-pool switch' or 'render-pool router update' to set target URL."
        }),
        {
          status: 503,
          headers: { "Content-Type": "application/json" }
        }
      );
    }

    // Strip trailing slashes
    backendUrl = backendUrl.replace(/\/+$/, "");

    // 2. Clone request and re-point target URL
    const originalUrl = new URL(request.url);
    const targetUrl = new URL(originalUrl.pathname + originalUrl.search, backendUrl);

    const headers = new Headers(request.headers);
    // Forward original host and IP info
    headers.set("X-Forwarded-Host", originalUrl.host);
    headers.set("X-Forwarded-Proto", originalUrl.protocol.replace(":", ""));
    headers.set("Host", new URL(backendUrl).host);

    const newRequest = new Request(targetUrl.toString(), {
      method: request.method,
      headers: headers,
      body: request.body,
      redirect: "follow"
    });

    try {
      const response = await fetch(newRequest);
      return response;
    } catch (err) {
      return new Response(
        JSON.stringify({
          error: "Failed to connect to active Render backend instance.",
          details: err.message,
          target: backendUrl
        }),
        {
          status: 502,
          headers: { "Content-Type": "application/json" }
        }
      );
    }
  }
};
