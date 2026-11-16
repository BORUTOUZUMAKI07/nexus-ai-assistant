"use client";

import React, { useMemo, useState } from "react";
import { RefreshCw, Smartphone, Tablet, Monitor } from "lucide-react";
import { MarkdownRenderer } from "@/features/chat/ChatArea";

interface ArtifactPreviewProps {
  content: string;
  language: string;
  title: string;
}

type DeviceMode = "desktop" | "tablet" | "mobile";

/**
 * Builds a self-contained HTML document for the sandboxed iframe.
 * Injects Tailwind CSS for modern component styling, React 18, and Babel Standalone
 * for compiling JSX/TSX on the fly in the client's browser.
 */
function buildSandboxDoc(code: string, language: string): string {
  const lang = (language || "").toLowerCase().trim();

  // Pure SVG
  if (lang === "svg" || code.trim().startsWith("<svg")) {
    return `<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
      body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; background: #09090b; color: #fafafa; font-family: ui-sans-serif, system-ui, sans-serif; }
      svg { max-width: 95vw; max-height: 90vh; }
    </style>
  </head>
  <body>
    ${code}
  </body>
</html>`;
  }

  // Pure HTML
  if (lang === "html" || code.includes("<!DOCTYPE html>") || (code.includes("<html") && code.includes("</html>"))) {
    // If it's already a full HTML document, ensure Tailwind and meta are included
    if (code.includes("<!DOCTYPE html>") || code.includes("<html")) {
      if (!code.includes("tailwindcss.com")) {
        return code.replace("<head>", '<head><script src="https://cdn.tailwindcss.com"></script>');
      }
      return code;
    }

    return `<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
      body { margin: 0; padding: 1.5rem; background: #ffffff; color: #09090b; font-family: ui-sans-serif, system-ui, sans-serif; }
      @media (prefers-color-scheme: dark) {
        body { background: #09090b; color: #fafafa; }
      }
    </style>
  </head>
  <body>
    ${code}
  </body>
</html>`;
  }

  // React / JSX / TSX
  if (["jsx", "tsx", "react", "javascript", "typescript", "js", "ts"].includes(lang) || code.includes("export default") || code.includes("function") || code.includes("const App")) {
    // Clean imports from the code snippet so Babel standalone can run it
    const cleanCode = code
      .replace(/import\s+.*?\s+from\s+['"].*?['"];?/g, "")
      .replace(/export\s+default\s+/g, "const App = ")
      .replace(/export\s+/g, "");

    return `<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
    <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
    <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
    <style>
      body { margin: 0; padding: 1rem; background: #ffffff; color: #09090b; font-family: ui-sans-serif, system-ui, sans-serif; }
      @media (prefers-color-scheme: dark) {
        body { background: #09090b; color: #fafafa; }
      }
    </style>
  </head>
  <body>
    <div id="root"></div>
    <script type="text/babel">
      try {
        ${cleanCode}

        // Determine which component to mount
        const ComponentToMount = (typeof App !== 'undefined') ? App : 
          (typeof Component !== 'undefined') ? Component : 
          (typeof Dashboard !== 'undefined') ? Dashboard : 
          () => <div className="p-4 text-amber-500 font-mono text-sm">Please define an App or Component to render.</div>;

        const root = ReactDOM.createRoot(document.getElementById('root'));
        root.render(React.createElement(ComponentToMount));
      } catch (err) {
        document.getElementById('root').innerHTML = '<div style="color: #ef4444; padding: 1rem; font-family: monospace; font-size: 13px; background: rgba(239, 68, 68, 0.1); border-radius: 8px;"><strong>Rendering Error:</strong> ' + err.message + '</div>';
      }
    </script>
  </body>
</html>`;
  }

  // Fallback HTML shell
  return `<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
      body { margin: 0; padding: 1.5rem; background: #ffffff; color: #09090b; font-family: ui-sans-serif, system-ui, sans-serif; }
      @media (prefers-color-scheme: dark) {
        body { background: #09090b; color: #fafafa; }
      }
    </style>
  </head>
  <body>
    <pre class="p-4 bg-zinc-100 dark:bg-zinc-900 rounded-lg text-sm font-mono overflow-auto">${code
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")}</pre>
  </body>
</html>`;
}

export function ArtifactPreview({
  content,
  language,
  title,
}: ArtifactPreviewProps): React.ReactElement {
  const [device, setDevice] = useState<DeviceMode>("desktop");
  const [refreshKey, setRefreshKey] = useState(0);

  const lang = (language || "").toLowerCase().trim();
  const isMarkdown = ["markdown", "md"].includes(lang);

  const srcDoc = useMemo(() => {
    if (isMarkdown) return "";
    return buildSandboxDoc(content, lang);
  }, [content, lang, isMarkdown]);

  const deviceWidthClass = {
    desktop: "w-full",
    tablet: "w-[768px] mx-auto shadow-2xl border-x border-[var(--border-subtle)]",
    mobile: "w-[375px] mx-auto shadow-2xl border-x border-[var(--border-subtle)]",
  }[device];

  if (isMarkdown) {
    return (
      <div className="p-6 max-w-4xl mx-auto overflow-auto h-full">
        <MarkdownRenderer content={content} />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full w-full bg-[var(--bg-main)]">
      {/* Interactive Controls Bar */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] text-xs select-none">
        {/* Device Viewport Toggles */}
        <div className="flex items-center gap-1 bg-[var(--bg-surface-elevated)] p-0.5 rounded border border-[var(--border-subtle)]">
          <button
            onClick={() => setDevice("desktop")}
            className={`p-1 rounded flex items-center gap-1 transition-colors ${
              device === "desktop"
                ? "bg-[var(--accent)] text-white shadow-sm"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}
            title="Desktop view (100%)"
          >
            <Monitor className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => setDevice("tablet")}
            className={`p-1 rounded flex items-center gap-1 transition-colors ${
              device === "tablet"
                ? "bg-[var(--accent)] text-white shadow-sm"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}
            title="Tablet view (768px)"
          >
            <Tablet className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => setDevice("mobile")}
            className={`p-1 rounded flex items-center gap-1 transition-colors ${
              device === "mobile"
                ? "bg-[var(--accent)] text-white shadow-sm"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}
            title="Mobile view (375px)"
          >
            <Smartphone className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setRefreshKey((k) => k + 1)}
            className="flex items-center gap-1 px-2 py-1 rounded border border-[var(--border-subtle)] text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)] transition-colors text-[11px]"
            title="Reload sandbox"
          >
            <RefreshCw className="w-3 h-3" />
            Reload
          </button>
        </div>
      </div>

      {/* Sandboxed Interactive Frame */}
      <div className="flex-1 w-full h-full overflow-hidden flex items-center justify-center bg-[var(--bg-subtle)]">
        <iframe
          key={refreshKey}
          srcDoc={srcDoc}
          title={title || "Artifact Live Sandbox"}
          sandbox="allow-scripts allow-modals"
          className={`h-full transition-all duration-200 border-0 bg-white dark:bg-zinc-950 ${deviceWidthClass}`}
        />
      </div>
    </div>
  );
}
