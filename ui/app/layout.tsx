import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import type { ReactNode } from "react";

import { Chrome } from "@/components/Chrome";
import { ChartDefs } from "@/components/ui";

import "./globals.css";

// Self-hosted at build time by next/font: the browser fetches nothing from Google.
const sans = Geist({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "FORGE control plane",
  description: "Missions, agents, evaluations, learning, governance, and costs.",
};

export const viewport: Viewport = { themeColor: "#05070d", colorScheme: "dark" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>
        <div className="backdrop" aria-hidden="true">
          <div className="aurora a" />
          <div className="aurora b" />
          <div className="aurora c" />
          <div className="grid" />
          <div className="grain" />
        </div>
        <ChartDefs />
        <Chrome>{children}</Chrome>
      </body>
    </html>
  );
}
