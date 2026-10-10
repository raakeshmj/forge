import { describe, expect, it } from "vitest";

import { active, crumbs, LINKS, search } from "./nav";

describe("active", () => {
  it("matches the dashboard only exactly", () => {
    expect(active("/")?.label).toBe("Dashboard");
    expect(active("/unknown")).toBeUndefined();
  });

  it("matches sections and their detail pages", () => {
    expect(active("/missions")?.label).toBe("Missions");
    expect(active("/missions/1234abcd-0000")?.label).toBe("Missions");
    expect(active("/agents/42")?.label).toBe("Agents");
    expect(active("/missions/new")?.label).toBe("New mission"); // the longer prefix wins
  });

  it("does not match a section that merely shares a prefix", () => {
    expect(active("/agentsx")).toBeUndefined();
  });
});

describe("crumbs", () => {
  it("names the section, then a short id for detail pages", () => {
    expect(crumbs("/costs")).toEqual([{ label: "Costs" }]);
    expect(crumbs("/missions/1234abcd-5678-0000")).toEqual([
      { label: "Missions", href: "/missions" },
      { label: "1234abcd" },
    ]);
  });
});

describe("search", () => {
  it("returns every link for an empty query", () => {
    expect(search("  ")).toHaveLength(LINKS.length);
  });

  it("matches every word against labels and hints, ignoring case", () => {
    expect(search("COST").map((link) => link.label)).toEqual(["Costs"]);
    expect(search("audit log").map((link) => link.label)).toEqual(["Policies"]);
    expect(search("zzz")).toEqual([]);
  });
});
