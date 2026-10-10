import type { ReactNode } from "react";

export function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}

export function Status({ value }: { value: string }) {
  return <span className={`badge s-${value}`}>{value.replaceAll("_", " ").toLowerCase()}</span>;
}

export function Bar({ ratio, title }: { ratio: number; title?: string }) {
  const width = Math.max(0, Math.min(1, ratio)) * 100;
  return (
    <div className="bar" title={title}>
      <span style={{ width: `${width}%` }} />
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Header({ title, sub }: { title: string; sub?: ReactNode }) {
  return (
    <>
      <h1>{title}</h1>
      {sub ? <p className="sub">{sub}</p> : null}
    </>
  );
}
