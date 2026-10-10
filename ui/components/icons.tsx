import type { ReactNode } from "react";

import type { IconName } from "@/lib/nav";

// Inline stroke icons (24 x 24 grid, 1.6 px strokes): no icon library, nothing fetched.
const PATHS: Record<
  IconName | "search" | "spark" | "arrow" | "bolt" | "shield" | "clock" | "coin" | "layers",
  ReactNode
> = {
  pulse: <path d="M3 12h4l2.5-6 4 12 2.5-6H21" />,
  launch: (
    <>
      <path d="M14 4c3-1 5.5-1 6-0.5S20 7 19 10l-6 6-5-5z" />
      <path d="M8 11l-3 .5L3 14l4 1M13 16l-.5 3L10 21l-1-4" />
      <circle cx="15.5" cy="8.5" r="1.4" />
    </>
  ),
  missions: (
    <>
      <path d="M12 3l7 4v6c0 4-3 7-7 8-4-1-7-4-7-8V7z" />
      <path d="M9 12l2 2 4-4" />
    </>
  ),
  approvals: (
    <>
      <rect x="4" y="4" width="16" height="16" rx="4" />
      <path d="M8.5 12.5l2.5 2.5 5-6" />
    </>
  ),
  agents: (
    <>
      <rect x="5" y="8" width="14" height="11" rx="3" />
      <path d="M12 4v4M9 13h.01M15 13h.01M9.5 16h5" />
    </>
  ),
  graph: (
    <>
      <circle cx="6" cy="6" r="2.2" />
      <circle cx="18" cy="8" r="2.2" />
      <circle cx="9" cy="18" r="2.2" />
      <path d="M8 7l8 1M7 8l1.6 8M16.4 9.6L10.7 16.5" />
    </>
  ),
  skills: <path d="M12 3l2.6 5.6 6 .7-4.5 4.1 1.2 6L12 16.4 6.7 19.4l1.2-6L3.4 9.3l6-.7z" />,
  evaluations: (
    <>
      <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
    </>
  ),
  benchmarks: (
    <>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="12" cy="12" r="0.6" />
    </>
  ),
  observability: (
    <>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  costs: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M15 9.2c-.6-1-1.7-1.5-3-1.5-1.7 0-3 .9-3 2.1 0 2.9 6 1.5 6 4.4 0 1.2-1.3 2.1-3 2.1-1.4 0-2.6-.6-3.1-1.6M12 6v1.7M12 16.3V18" />
    </>
  ),
  policies: (
    <>
      <path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z" />
      <path d="M12 8v5M12 16h.01" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="M16 16l4.5 4.5" />
    </>
  ),
  spark: <path d="M12 2v5M12 17v5M2 12h5M17 12h5M5 5l3 3M16 16l3 3M19 5l-3 3M8 16l-3 3" />,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  bolt: <path d="M13 2L4 14h7l-1 8 9-12h-7z" />,
  shield: <path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  coin: (
    <>
      <ellipse cx="12" cy="7" rx="7" ry="3" />
      <path d="M5 7v5c0 1.7 3.1 3 7 3s7-1.3 7-3V7M5 12v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5" />
    </>
  ),
  layers: (
    <>
      <path d="M12 3l9 5-9 5-9-5z" />
      <path d="M3 13l9 5 9-5" />
    </>
  ),
};

export type Glyph = keyof typeof PATHS;

export function Icon({ name, size = 16 }: { name: Glyph; size?: number }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}

/** FORGE's mark: an anvil-spark hexagon, drawn with the accent gradient. */
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className="mark">
      <defs>
        <linearGradient id="forge-mark" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--a1)" />
          <stop offset="0.55" stopColor="var(--a2)" />
          <stop offset="1" stopColor="var(--a3)" />
        </linearGradient>
      </defs>
      <path d="M16 2l12 7v14l-12 7-12-7V9z" fill="none" stroke="url(#forge-mark)" strokeWidth="1.8" />
      <path d="M16 8l6 3.5v7L16 22l-6-3.5v-7z" fill="url(#forge-mark)" opacity="0.9" />
      <path d="M16 8v14M10 11.5l12 7M22 11.5l-12 7" stroke="#05070d" strokeWidth="1" opacity="0.55" />
    </svg>
  );
}
