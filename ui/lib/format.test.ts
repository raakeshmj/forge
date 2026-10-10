import { describe, expect, it } from "vitest";

import { usd } from "./format";

describe("usd", () => {
  it("formats micro-USD with precision that fits the size", () => {
    expect(usd(0)).toBe("$0");
    expect(usd(1_017)).toBe("$0.001017");
    expect(usd(21_400)).toBe("$0.0214");
    expect(usd(123_450_000)).toBe("$123.45");
  });

  it("puts the sign before the currency symbol", () => {
    expect(usd(-483)).toBe("-$0.000483");
    expect(usd(-21_400)).toBe("-$0.0214");
  });
});
