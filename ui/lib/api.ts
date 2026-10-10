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
): Promise<{ status: number; detail: string; body: unknown }> {
  return send(path, body, { authorization: `Bearer ${operatorToken}` });
}

/**
 * For writes the API accepts from any client (submitting a mission, registering an agent):
 * the UI sends exactly what `curl` would, with no credentials of its own (ADR 0012).
 */
export async function apiCreate(
  path: string,
  body: unknown,
): Promise<{ status: number; detail: string; body: unknown }> {
  return send(path, body, {});
}

async function send(
  path: string,
  body: unknown,
  headers: Record<string, string>,
): Promise<{ status: number; detail: string; body: unknown }> {
  const response = await fetch(`${FORGE_API_URL}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  const text = await response.text();
  let detail = text;
  let parsed: unknown = undefined;
  try {
    parsed = JSON.parse(text);
    const value = (parsed as { detail?: unknown }).detail;
    if (typeof value === "string") detail = value;
    else if (Array.isArray(value)) detail = value.map((v) => String((v as { msg?: unknown }).msg ?? v)).join("; ");
  } catch {
    // not JSON: keep the raw text
  }
  return { status: response.status, detail, body: parsed };
}
