import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { get } from "@vercel/edge-config";

/**
 * Vercel Edge Middleware: Routes /api/* directly to the active Render backend.
 * Uses Vercel Edge Config to dynamically retrieve the active backend URL.
 */
export async function middleware(request: NextRequest) {
  if (request.nextUrl.pathname.startsWith("/api/")) {
    let targetBackend = process.env.RENDER_BACKEND_URL || "https://your-primary-service.onrender.com";

    // Attempt to read dynamic active URL from Edge Config
    try {
      if (process.env.EDGE_CONFIG) {
        const edgeUrl = await get<string>("active_render_url");
        if (edgeUrl) {
          targetBackend = edgeUrl;
        }
      }
    } catch (err) {
      console.warn("Failed to read active URL from Edge Config:", err);
    }

    const cleanBackend = targetBackend.replace(/\/+$/, "");
    const targetUrl = new URL(
      request.nextUrl.pathname + request.nextUrl.search,
      cleanBackend
    );

    return NextResponse.rewrite(targetUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/api/:path*"],
};
