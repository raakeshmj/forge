import { afterEach, describe, expect, it, vi } from "vitest";

import { decide } from "./actions";

const ID = "6f1d2c3b-1111-4222-8333-944455566677";
const TOKEN = "op-secret-token-value-0123456789abcdef";

function form(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) data.set(key, value);
  return data;
}

function respond(status: number, body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status }));
}

afterEach(() => vi.unstubAllGlobals());

describe("decide", () => {
  it("forwards the decision once with the operator's own token as a bearer header", async () => {
    const fetchMock = respond(200, { id: ID, status: "APPROVED" });
    vi.stubGlobal("fetch", fetchMock);

    const result = await decide(null, form({ approval_id: ID, decision: "approve", reason: "ok", operator_token: TOKEN }));

    expect(result).toEqual({ ok: true, message: "Recorded: approve." });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(new RegExp(`/approvals/${ID}/decision$`));
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${TOKEN}`);
    expect(JSON.parse(String(init.body))).toEqual({ decision: "approve", reason: "ok" }); // no token in the body
  });

  it("never echoes the token, even when the API rejects it", async () => {
    vi.stubGlobal("fetch", respond(401, { detail: "an operator token is required" }));

    const result = await decide(null, form({ approval_id: ID, decision: "deny", reason: "no", operator_token: TOKEN }));

    expect(result.ok).toBe(false);
    expect(result.message).not.toContain(TOKEN);
    expect(result.message).toContain("not accepted");
  });

  it("refuses malformed input without calling the API", async () => {
    const fetchMock = respond(200, {});
    vi.stubGlobal("fetch", fetchMock);

    for (const fields of [
      { approval_id: "../../policies", decision: "approve", reason: "x", operator_token: TOKEN },
      { approval_id: ID, decision: "promote", reason: "x", operator_token: TOKEN },
      { approval_id: ID, decision: "approve", reason: " ", operator_token: TOKEN },
      { approval_id: ID, decision: "approve", reason: "x", operator_token: "" },
    ]) {
      expect((await decide(null, form(fields))).ok).toBe(false);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports a conflict, such as an already-decided request", async () => {
    vi.stubGlobal("fetch", respond(409, { detail: "approval x is already APPROVED" }));

    const result = await decide(null, form({ approval_id: ID, decision: "approve", reason: "x", operator_token: TOKEN }));

    expect(result).toEqual({ ok: false, message: "Not recorded (409): approval x is already APPROVED" });
  });
});
