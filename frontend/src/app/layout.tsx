import type { Metadata, Viewport } from "next";
import { Inter, Geist_Mono } from "next/font/google";
import { THEME_INIT_SCRIPT } from "@/lib/theme";
import { AppToaster } from "@/components/ui/sonner";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const mono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

/**
 * Canonical origin, read from the environment rather than hardcoded.
 *
 * `metadataBase` is what lets Next resolve the relative `openGraph.url` below
 * into an absolute one. Without it, OG crawlers receive a *relative* URL, which
 * they cannot resolve — so the link preview silently degrades to a bare title
 * with no image on every social post. `VERCEL_URL` is set automatically by
 * Vercel; the localhost fallback keeps `next build` from warning locally.
 */
const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ??
  (process.env.VERCEL_URL ? `https://${process.env.VERCEL_URL}` : "http://localhost:3000");

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: "Nexus AI — Agentic Production Assistant",
  description:
    "Nexus AI is an agentic production assistant with live web research, hybrid RAG knowledge, code execution, and human-in-the-loop approvals.",
  applicationName: "Nexus AI",
  // `generator` is what `poweredByHeader: false` removes from HTTP responses;
  // this removes it from the rendered document too.
  generator: undefined,
  // An authenticated app. `noindex` is a crawl hint, not an access control —
  // the real gate is the session check in src/proxy.ts and the backend's bearer
  // auth — but there is no reason to have the signed-in shell indexed at all.
  robots: { index: false, follow: false },
  openGraph: {
    type: "website",
    siteName: "Nexus AI",
    title: "Nexus AI — Agentic Production Assistant",
    description:
      "Agentic production assistant with live web research, hybrid RAG knowledge, code execution, and human-in-the-loop approvals.",
    url: "/",
  },
  twitter: {
    card: "summary",
    title: "Nexus AI — Agentic Production Assistant",
    description:
      "Agentic production assistant with live web research, hybrid RAG knowledge, code execution, and human-in-the-loop approvals.",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  /**
   * Single static value rather than a `prefers-color-scheme` media query, and
   * that is deliberate.
   *
   * The Refero system defaults to Paper (light) whenever the user has not
   * chosen a mode — `THEME_INIT_SCRIPT` in lib/theme.ts falls back to "light",
   * and "system" is opt-in. So a first-time visitor and any visitor who picked
   * light sees `#fafaf9`, even on a device whose OS is set to dark. A media
   * query would paint the browser chrome Void-dark for exactly those users,
   * which is worse than picking one honest default: it mismatches the page the
   * user is actually looking at.
   *
   * This value is `--bg-main` from the `:root` block in globals.css. The two
   * must be changed together; they are the same token.
   */
  themeColor: "#fafaf9",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${mono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        {/* Paints the stored theme before first render — no light/dark flash. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-full flex flex-col">
        {children}
        <AppToaster />
      </body>
    </html>
  );
}
