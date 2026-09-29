import type { Metadata } from "next";
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

export const metadata: Metadata = {
  title: "Nexus AI — Agentic Production Assistant",
  description:
    "Nexus AI is an agentic production assistant with live web research, hybrid RAG knowledge, code execution, and human-in-the-loop approvals.",
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
