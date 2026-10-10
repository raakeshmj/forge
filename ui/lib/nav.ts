// The control plane's navigation: one definition for the sidebar, breadcrumbs, and the
// command palette.

export type IconName =
  | "pulse"
  | "launch"
  | "missions"
  | "approvals"
  | "agents"
  | "graph"
  | "skills"
  | "evaluations"
  | "benchmarks"
  | "observability"
  | "costs"
  | "policies";

export interface NavLink {
  href: string;
  label: string;
  icon: IconName;
  hint: string;
}

export const NAV: { group: string; links: NavLink[] }[] = [
  {
    group: "Operate",
    links: [
      { href: "/", label: "Dashboard", icon: "pulse", hint: "System overview" },
      { href: "/missions/new", label: "New mission", icon: "launch", hint: "Give FORGE an objective" },
      { href: "/missions", label: "Missions", icon: "missions", hint: "Every mission, newest first" },
      { href: "/approvals", label: "Approvals", icon: "approvals", hint: "Calls waiting for a human" },
    ],
  },
  {
    group: "Agents",
    links: [
      { href: "/agents", label: "Agents", icon: "agents", hint: "Registered agents and versions" },
      { href: "/capabilities", label: "Capability graph", icon: "graph", hint: "Capabilities and competency" },
      { href: "/skills", label: "Skills", icon: "skills", hint: "Skills and learning cycles" },
    ],
  },
  {
    group: "Measure",
    links: [
      { href: "/evaluations", label: "Evaluations", icon: "evaluations", hint: "Scores on every dimension" },
      { href: "/benchmarks", label: "Benchmarks", icon: "benchmarks", hint: "Ground-truth runs" },
      { href: "/observability", label: "Observability", icon: "observability", hint: "Latency, tokens, caching" },
      { href: "/costs", label: "Costs", icon: "costs", hint: "Spend by agent, model, day" },
    ],
  },
  {
    group: "Govern",
    links: [{ href: "/policies", label: "Policies", icon: "policies", hint: "Rules, tools, audit log" }],
  },
];

export const LINKS: NavLink[] = NAV.flatMap((group) => group.links);

/** The nav link a path belongs to: exact for "/", otherwise the longest matching prefix. */
export function active(pathname: string): NavLink | undefined {
  if (pathname === "/") return LINKS[0];
  return LINKS.filter(
    (link) => link.href !== "/" && (pathname === link.href || pathname.startsWith(`${link.href}/`)),
  ).sort((a, b) => b.href.length - a.href.length)[0];
}

export interface Crumb {
  label: string;
  href?: string;
}

/** Breadcrumbs for a path: the section, then a shortened id for a detail page. */
export function crumbs(pathname: string): Crumb[] {
  const section = active(pathname);
  if (!section) return [{ label: "FORGE" }];
  const rest = pathname.slice(section.href.length).split("/").filter(Boolean);
  if (!rest.length) return [{ label: section.label }];
  return [{ label: section.label, href: section.href }, { label: (rest[0] ?? "").slice(0, 8) }];
}

/** Command-palette matching: every query word must appear in the label or hint. */
export function search(query: string, links: readonly NavLink[] = LINKS): NavLink[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [...links];
  return links.filter((link) => {
    const text = `${link.label} ${link.hint}`.toLowerCase();
    return words.every((word) => text.includes(word));
  });
}
