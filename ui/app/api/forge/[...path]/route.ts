// Read-only proxy from the browser to the FORGE API (ADR 0012). GET only: every write goes
// through a server action that carries an operator's token. Streams pass through unbuffered.
import { FORGE_API_URL } from "@/lib/api";

export const dynamic = "force-dynamic";

export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const search = new URL(request.url).search;
  const upstream = await fetch(`${FORGE_API_URL}/${path.map(encodeURIComponent).join("/")}${search}`, {
    cache: "no-store",
    headers: { accept: request.headers.get("accept") ?? "application/json" },
    signal: request.signal,
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
      "cache-control": "no-cache",
    },
  });
}
