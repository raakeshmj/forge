import type { ReactNode } from "react";

import { Icon, type Glyph } from "@/components/icons";
import { columns, ring, segments, unit } from "@/lib/charts";

export type Tone = "accent" | "ok" | "warn" | "bad" | "wait" | "muted";

/** Gradients shared by every chart on the page (referenced as url(#g-<tone>)). */
export function ChartDefs() {
  const stops: [Tone, string, string][] = [
    ["accent", "var(--a1)", "var(--a3)"],
    ["ok", "#34d399", "#22d3ee"],
    ["warn", "#fbbf24", "#fb7185"],
    ["bad", "#fb7185", "#f43f5e"],
    ["wait", "#c084fc", "#818cf8"],
    ["muted", "#475569", "#334155"],
  ];
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true">
      <defs>
        {stops.map(([tone, from, to]) => (
          <linearGradient key={tone} id={`g-${tone}`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor={from} />
            <stop offset="1" stopColor={to} />
          </linearGradient>
        ))}
        {stops.map(([tone, from, to]) => (
          <linearGradient key={`v-${tone}`} id={`gv-${tone}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={from} />
            <stop offset="1" stopColor={to} stopOpacity="0.35" />
          </linearGradient>
        ))}
      </defs>
    </svg>
  );
}

export function Stat({
  label,
  value,
  hint,
  icon,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: Glyph;
  tone?: Tone;
}) {
  return (
    <div className={`stat${tone ? ` t-${tone}` : ""}`}>
      <div className="label">
        {icon ? <Icon name={icon} size={13} /> : null}
        {label}
      </div>
      <div className="value">{value}</div>
      {hint ? <div className="hint">{hint}</div> : null}
    </div>
  );
}

const LIVE = new Set(["RUNNING", "PLANNING", "READY", "VALIDATING", "WAITING_APPROVAL", "PENDING", "BLOCKED"]);

export function Status({ value }: { value: string }) {
  return (
    <span className={`badge s-${value}${LIVE.has(value) ? " live" : ""}`}>
      <span className="dot" />
      {value.replaceAll("_", " ").toLowerCase()}
    </span>
  );
}

export function Bar({ ratio, title, tone = "accent" }: { ratio: number; title?: string; tone?: Tone }) {
  return (
    <div className={`bar t-${tone}`} title={title}>
      <span style={{ width: `${unit(ratio) * 100}%` }} />
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="empty">
      <Icon name="spark" size={14} />
      <span>{children}</span>
    </div>
  );
}

export function Header({
  title,
  sub,
  eyebrow,
  aside,
}: {
  title: string;
  sub?: ReactNode;
  eyebrow?: string;
  aside?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div>
        {eyebrow ? <div className="eyebrow">{eyebrow}</div> : null}
        <h1>{title}</h1>
        {sub ? <div className="sub">{sub}</div> : null}
      </div>
      {aside ? <div className="page-aside">{aside}</div> : null}
    </header>
  );
}

/** A titled block of a page; headings are numbered by CSS like a flight checklist. */
export function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      <div className="section-head">
        <h2>{title}</h2>
        {aside ? <div className="section-aside">{aside}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function Panel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={`panel hud${className ? ` ${className}` : ""}`}>{children}</div>;
}

/** A ring gauge: `ratio` of the circle filled, with the reading in the middle. */
export function Gauge({
  ratio,
  label,
  reading,
  caption,
  tone = "accent",
  size = 132,
}: {
  ratio: number | null;
  label: string;
  reading: ReactNode;
  caption?: ReactNode;
  tone?: Tone;
  size?: number;
}) {
  const stroke = 9;
  const arc = ring(ratio ?? 0, size, stroke);
  const center = size / 2;
  return (
    <figure className={`gauge t-${tone}`} style={{ width: size }}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={`${label}: ${String(reading)}`}
      >
        <circle cx={center} cy={center} r={arc.radius} className="gauge-track" strokeWidth={stroke} fill="none" />
        <circle
          cx={center}
          cy={center}
          r={arc.radius}
          className="gauge-ticks"
          strokeWidth={stroke + 6}
          fill="none"
          strokeDasharray="1 5.1"
        />
        {ratio === null ? null : (
          <circle
            cx={center}
            cy={center}
            r={arc.radius}
            fill="none"
            stroke={`url(#g-${tone})`}
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={arc.dash}
            transform={`rotate(-90 ${center} ${center})`}
            className="gauge-arc"
          />
        )}
      </svg>
      <div className="gauge-reading">
        <span className="gauge-value">{reading}</span>
        <span className="gauge-label">{label}</span>
      </div>
      {caption ? <figcaption>{caption}</figcaption> : null}
    </figure>
  );
}

/** A column chart; each bar carries a native tooltip with its exact label. */
export function Columns({
  rows,
  height = 120,
  tone = "accent",
  format = (value: number) => String(value),
}: {
  rows: { key: string; value: number; label?: string }[];
  height?: number;
  tone?: Tone;
  format?: (value: number) => string;
}) {
  const width = 600;
  const bars = columns(rows, width, height, 6);
  return (
    <div className="columns">
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label="Column chart">
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1="0" x2={width} y1={height * f} y2={height * f} className="columns-rule" />
        ))}
        {bars.map((bar, index) => (
          <rect
            key={bar.key}
            x={bar.x}
            y={bar.y}
            width={bar.width}
            height={bar.height}
            rx="3"
            fill={`url(#gv-${tone})`}
          >
            <title>{`${rows[index]?.label ?? bar.key}: ${format(bar.value)}`}</title>
          </rect>
        ))}
      </svg>
      <div className="columns-axis mono">
        <span>{rows[0]?.label ?? rows[0]?.key}</span>
        {rows.length > 1 ? <span>{rows.at(-1)?.label ?? rows.at(-1)?.key}</span> : null}
      </div>
    </div>
  );
}

const STATUS_TONE: Record<string, Tone> = {
  COMPLETED: "ok",
  FAILED: "bad",
  CANCELLED: "muted",
  RUNNING: "accent",
  PLANNING: "accent",
  READY: "accent",
  WAITING_APPROVAL: "wait",
  BLOCKED: "wait",
};

/** A stacked bar of shares (e.g. missions by status) with a legend. */
export function Distribution({ rows }: { rows: { key: string; count: number }[] }) {
  const parts = segments(rows);
  if (!parts.length) return <div className="distribution empty-line mono">no data yet</div>;
  return (
    <div className="distribution">
      <div className="distribution-bar">
        {parts.map((part) => (
          <span
            key={part.key}
            className={`t-${STATUS_TONE[part.key] ?? "muted"}`}
            style={{ flexGrow: part.share }}
            title={`${part.key}: ${part.count}`}
          />
        ))}
      </div>
      <div className="legend">
        {parts.map((part) => (
          <span key={part.key} className={`t-${STATUS_TONE[part.key] ?? "muted"}`}>
            <i />
            {part.key.replaceAll("_", " ").toLowerCase()} <b className="mono">{part.count}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

/** An inline meter with its numeric reading, for scores in tables. */
export function Meter({ value, tone }: { value: number | null; tone?: Tone }) {
  if (value === null) return <span className="meter na mono">n/a</span>;
  const t: Tone = tone ?? (value >= 0.8 ? "ok" : value >= 0.5 ? "warn" : "bad");
  return (
    <span className={`meter t-${t}`}>
      <span className="meter-track">
        <span style={{ width: `${unit(value) * 100}%` }} />
      </span>
      <span className="mono">{value.toFixed(2)}</span>
    </span>
  );
}
