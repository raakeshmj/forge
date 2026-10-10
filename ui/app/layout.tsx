import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "FORGE control plane",
  description: "Missions, agents, evaluations, learning, governance, and costs.",
};

const NAV: [string, [string, string][]][] = [
  ["Operate", [["/", "Dashboard"], ["/missions", "Missions"], ["/approvals", "Approvals"]]],
  ["Agents", [["/agents", "Agents"], ["/capabilities", "Capability graph"], ["/skills", "Skills"]]],
  ["Measure", [["/evaluations", "Evaluations"], ["/benchmarks", "Benchmarks"], ["/observability", "Observability"], ["/costs", "Costs"]]],
  ["Govern", [["/policies", "Policies"]]],
];

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <nav className="side" aria-label="Control plane">
            <div className="brand">FORGE</div>
            {NAV.map(([group, links]) => (
              <div key={group}>
                <div className="group">{group}</div>
                {links.map(([href, label]) => (
                  <Link key={href} href={href}>
                    {label}
                  </Link>
                ))}
              </div>
            ))}
          </nav>
          <main>{children}</main>
        </div>
      </body>
    </html>
  );
}
