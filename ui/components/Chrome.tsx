"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Icon, Mark } from "@/components/icons";
import { active, crumbs, NAV, search } from "@/lib/nav";

interface Health {
  status: string;
  checks: Record<string, { status: string }>;
}

/** Polls the API's readiness through the read-only proxy: the sidebar's heartbeat. */
function useHealth(intervalMs = 10_000): Health | "down" | null {
  const [health, setHealth] = useState<Health | "down" | null>(null);
  useEffect(() => {
    let stopped = false;
    const check = async () => {
      try {
        const response = await fetch("/api/forge/health/ready", { cache: "no-store" });
        const body = (await response.json()) as Health;
        if (!stopped) setHealth(body);
      } catch {
        if (!stopped) setHealth("down"); // the API is unreachable: shown, then retried
      }
    };
    void check();
    const timer = setInterval(check, intervalMs);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [intervalMs]);
  return health;
}

function Clock() {
  const [now, setNow] = useState<string | null>(null); // rendered after mount: no hydration mismatch
  useEffect(() => {
    const tick = () => setNow(new Date().toISOString().slice(11, 19));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, []);
  return <span className="clock mono">{now ?? "--:--:--"} UTC</span>;
}

/** Mounted only while open, so every opening starts with an empty query. */
function Palette({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const results = search(query);
  const go = (href: string) => {
    onClose();
    router.push(href);
  };
  return (
    <div className="palette-backdrop" onClick={onClose} role="presentation">
      <div className="palette" role="dialog" aria-modal="true" aria-label="Go to" onClick={(e) => e.stopPropagation()}>
        <div className="palette-input">
          <Icon name="search" />
          <input
            autoFocus
            value={query}
            placeholder="Jump to…"
            aria-label="Search pages"
            onChange={(event) => {
              setQuery(event.target.value);
              setCursor(0);
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setCursor((c) => Math.min(c + 1, Math.max(0, results.length - 1)));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setCursor((c) => Math.max(c - 1, 0));
              } else if (event.key === "Enter" && results[cursor]) {
                go(results[cursor].href);
              } else if (event.key === "Escape") {
                onClose();
              }
            }}
          />
          <kbd>esc</kbd>
        </div>
        <ul className="palette-results">
          {results.map((link, index) => (
            <li key={link.href}>
              <button
                type="button"
                className={index === cursor ? "on" : undefined}
                onMouseEnter={() => setCursor(index)}
                onClick={() => go(link.href)}
              >
                <Icon name={link.icon} />
                <span className="label">{link.label}</span>
                <span className="hint">{link.hint}</span>
                <Icon name="arrow" size={14} />
              </button>
            </li>
          ))}
          {results.length === 0 ? <li className="palette-empty">No page matches “{query}”.</li> : null}
        </ul>
      </div>
    </div>
  );
}

/** The application frame: sidebar, top bar, and command palette around every page. */
export function Chrome({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const here = active(pathname);
  const trail = crumbs(pathname);
  const health = useHealth();
  const [palette, setPalette] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const healthy = health !== null && health !== "down" && health.status === "ready";
  return (
    <div className="shell">
      <nav className="side" aria-label="Control plane">
        <Link href="/" className="brand">
          <Mark />
          <span>
            <span className="brand-name">FORGE</span>
            <span className="brand-sub">control plane</span>
          </span>
        </Link>
        <div className="side-scroll">
          {NAV.map(({ group, links }) => (
            <div key={group} className="side-group">
              <div className="group">{group}</div>
              {links.map((link) => (
                <Link key={link.href} href={link.href} className={here?.href === link.href ? "on" : undefined}>
                  <Icon name={link.icon} />
                  {link.label}
                </Link>
              ))}
            </div>
          ))}
        </div>
        <div className={`system ${health === null ? "unknown" : healthy ? "up" : "down"}`}>
          <div className="system-head">
            <span className="beacon" />
            {health === null ? "Connecting…" : healthy ? "All systems nominal" : "Degraded"}
          </div>
          <div className="system-checks mono">
            {health === null || health === "down" ? (
              <span>api {health === "down" ? "unreachable" : "…"}</span>
            ) : (
              ["api", ...Object.keys(health.checks)].map((name) => (
                <span key={name} className={name === "api" || health.checks[name]?.status === "ok" ? "ok" : "bad"}>
                  {name}
                </span>
              ))
            )}
          </div>
        </div>
      </nav>
      <div className="stage">
        <header className="top">
          <div className="crumbs">
            <span className="crumb-root">forge</span>
            {trail.map((crumb) => (
              <span key={crumb.label} className="crumb">
                <span className="sep">/</span>
                {crumb.href ? <Link href={crumb.href}>{crumb.label}</Link> : <span>{crumb.label}</span>}
              </span>
            ))}
          </div>
          <div className="top-right">
            <button type="button" className="jump" onClick={() => setPalette(true)}>
              <Icon name="search" size={14} />
              <span>Jump to</span>
              <kbd>⌘K</kbd>
            </button>
            <Clock />
          </div>
        </header>
        <main>{children}</main>
      </div>
      {palette ? <Palette onClose={() => setPalette(false)} /> : null}
    </div>
  );
}
