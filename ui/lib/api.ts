// Server-side access to the FORGE API (ADR 0012). Never imported by client components:
// the browser reaches the API only through the read-only /api/forge proxy.
import "server-only";

export const FORGE_API_URL = process.env.FORGE_API_URL ?? "http://127.0.0.1:18000";

export class ApiError extends Error {
  constructor(
    readonly path: string,
    readonly status: number,
    readonly detail: string,
  ) {
    super(`FORGE API ${path} returned ${status}: ${detail}`);
  }
}

export async function api<T>(path: string): Promise<T> {
  const response = await fetch(`${FORGE_API_URL}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new ApiError(path, response.status, await response.text());
  }
  return (await response.json()) as T;
}

/** For operator actions: a POST carrying the operator's own token, forwarded once. */
export async function apiPost(
  path: string,
  body: unknown,
  operatorToken: string,
): Promise<{ status: number; detail: string }> {
  const response = await fetch(`${FORGE_API_URL}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: { "content-type": "application/json", authorization: `Bearer ${operatorToken}` },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  let detail = text;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") detail = parsed.detail;
  } catch {
    // not JSON: keep the raw text
  }
  return { status: response.status, detail };
}
