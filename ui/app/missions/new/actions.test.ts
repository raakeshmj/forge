import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  redirect: vi.fn((url: string) => {
    throw new Error(`REDIRECT ${url}`); // like Next's redirect, it never returns
  }),
}));

import { launch, registerDemoAgent } from "./actions";

const AGENT = "6f1d2c3b-1111-4222-8333-944455566677";
const MISSION = "0a1b2c3d-4444-4555-8666-777788889999";

function form(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(fields)) data.set(key, value);
  return data;
}

function respond(status: number, body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status }));
}

const VALID = {
  objective: "Why did checkout latency increase?",
  target: `agent:${AGENT}`,
  budget_usd: "0.10",
  max_risk: "LOW",
};

afterEach(() => vi.unstubAllGlobals());

describe("launch", () => {
  it("submits the mission once, with no credentials, and opens it", async () => {
    const fetchMock = respond(201, { id: MISSION });
    vi.stubGlobal("fetch", fetchMock);

    await expect(launch(null, form(VALID))).rejects.toThrow(`REDIRECT /missions/${MISSION}`);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/missions$/);
    expect((init.headers as Record<string, string>).authorization).toBeUndefined();
    expect(JSON.parse(String(init.body))).toEqual({
      objective: "Why did checkout latency increase?",
      agent_id: AGENT,
      budget_micro_usd: 100_000,
      max_risk: "LOW",
    });
  });

  it("asks a planner agent to draft the task graph", async () => {
    const fetchMock = respond(201, { id: MISSION });
    vi.stubGlobal("fetch", fetchMock);

    await expect(launch(null, form({ ...VALID, target: `planner:${AGENT}` }))).rejects.toThrow("REDIRECT");

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body.planner_agent_id).toBe(AGENT);
    expect(body.agent_id).toBeUndefined();
  });

  it("refuses malformed input without calling the API", async () => {
    const fetchMock = respond(201, { id: MISSION });
    vi.stubGlobal("fetch", fetchMock);

    for (const fields of [
      { ...VALID, objective: "   " },
      { ...VALID, objective: "x".repeat(10_001) },
      { ...VALID, target: "agent:../../agents" },
      { ...VALID, target: `admin:${AGENT}` },
      { ...VALID, budget_usd: "0" },
      { ...VALID, budget_usd: "11" },
      { ...VALID, budget_usd: "lots" },
      { ...VALID, max_risk: "CRITICAL" },
    ]) {
      expect((await launch(null, form(fields))).ok).toBe(false);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports the API's reason when the mission is not accepted", async () => {
    vi.stubGlobal("fetch", respond(404, { detail: `agent ${AGENT} not found` }));

    expect(await launch(null, form(VALID))).toEqual({
      ok: false,
      message: `Not launched (404): agent ${AGENT} not found`,
    });
  });

  it("does not redirect to anything but a mission id", async () => {
    vi.stubGlobal("fetch", respond(201, { id: "https://elsewhere.example" }));

    expect((await launch(null, form(VALID))).ok).toBe(false);
  });
});

describe("registerDemoAgent", () => {
  it("registers the same demo agent RUNNING.md does", async () => {
    const fetchMock = respond(201, { agent_id: AGENT });
    vi.stubGlobal("fetch", fetchMock);

    expect(await registerDemoAgent()).toEqual({ ok: true, message: "Registered the demo investigator." });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/agents$/);
    expect(JSON.parse(String(init.body))).toMatchObject({ name: "investigator", model: { provider: "demo" } });
  });

  it("explains a refusal, such as a name already in use", async () => {
    vi.stubGlobal("fetch", respond(409, { detail: "agent investigator already exists" }));

    expect(await registerDemoAgent()).toEqual({
      ok: false,
      message: "Not registered (409): agent investigator already exists",
    });
  });
});
