import Link from "next/link";
import {
  ArrowRight,
  BarChart2,
  Brain,
  Check,
  ChevronRight,
  Cpu,
  FileText,
  Globe,
  Layers,
  MessageSquare,
  ShieldCheck,
  Sparkles,
  Terminal,
  Zap,
} from "lucide-react";

const GRADIENT_TEXT = "text-gradient";

function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <span
      className="grid place-items-center rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)] shadow-sm"
      style={{
        width: size,
        height: size,
      }}
      aria-hidden="true"
    >
      <Sparkles className="text-[var(--accent)]" size={size * 0.55} strokeWidth={2.2} />
    </span>
  );
}

function Navbar() {
  return (
    <header className="fixed inset-x-0 top-0 z-50 border-b border-[var(--border-subtle)] bg-[rgba(8,9,10,0.85)] backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <a href="#top" className="flex items-center gap-3">
          <BrandMark size={30} />
          <span className="text-[15px] font-semibold tracking-tight text-[var(--text-primary)]">
            Nexus{" "}
            <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 py-0.5 text-[11px] font-medium text-[var(--text-muted)]">
              AI
            </span>
          </span>
        </a>

        <nav className="hidden items-center gap-8 text-[13.5px] text-[var(--text-secondary)] md:flex">
          <a className="transition-colors hover:text-[var(--text-primary)]" href="#features">
            Features
          </a>
          <a className="transition-colors hover:text-[var(--text-primary)]" href="#models">
            Models
          </a>
          <a className="transition-colors hover:text-[var(--text-primary)]" href="#pricing">
            Pricing
          </a>
          <a className="transition-colors hover:text-[var(--text-primary)]" href="#faq">
            FAQ
          </a>
        </nav>

        <div className="flex items-center gap-3">
          <Link
            href="/signin"
            className="btn-ghost-pill px-4 py-1.5 text-[13px] hover:text-[var(--text-primary)]"
          >
            Sign in
          </Link>
          <Link
            href="/app"
            className="btn-electric group inline-flex items-center gap-2 px-5 py-2 text-[13px]"
          >
            Launch app
            <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
          </Link>
        </div>
      </div>
    </header>
  );
}

function HeroMockup() {
  return (
    <div
      className="relative mx-auto mt-16 w-full max-w-3xl overflow-hidden rounded-2xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] shadow-float"
      aria-hidden="true"
    >
      <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-5 py-3.5">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
        </div>
        <span className="flex items-center gap-1.5 text-[12px] text-[var(--text-muted)]">
          <MessageSquare className="h-3.5 w-3.5" />
          research thread
        </span>
        <span className="flex items-center gap-1.5 rounded-full border border-[var(--border-subtle)] px-2.5 py-1 text-[11px] text-[var(--text-muted)]">
          <Cpu className="h-3 w-3 text-[var(--accent-violet)]" />
          groq/qwen3.8-27b
        </span>
        <span className="flex items-center gap-1 rounded-full border border-[var(--border-subtle)] bg-[var(--bg-main)] px-2.5 py-1 text-[11px] text-[var(--text-faint)]">
          <span className="stream-dot" />
          <span className="stream-dot" />
          <span className="stream-dot" />
          streaming
        </span>
      </div>

      <div className="space-y-4 p-5 sm:p-7">
        <div
          className="reveal-up ml-auto w-fit max-w-[78%] rounded-2xl rounded-br-md bg-[var(--accent-soft)] px-4 py-2.5 text-[13.5px] leading-relaxed text-[var(--text-primary)]"
          style={{ animationDelay: "0ms" }}
        >
          Compare the Q3 revenue trends across our three markets and run the
          numbers for the new pricing model.
        </div>

        <div className="w-fit max-w-[92%] space-y-3">
          <div
            className="reveal-up rounded-2xl rounded-bl-md border border-[var(--border-subtle)] bg-[var(--bg-main)] px-4 py-3"
            style={{ animationDelay: "300ms" }}
          >
            <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-faint)]">
              Reasoning
            </p>
            <p className="text-[13px] leading-relaxed text-[var(--text-secondary)]">
              User asked for a cross-market comparison plus a pricing simulation —
              plan is <span className="text-[var(--text-primary)]">web research then
              sandboxed analysis</span>. No sensitive tools, so no approval needed.
            </p>
          </div>

          <div
            className="reveal-up rounded-2xl rounded-bl-md border border-[var(--border-subtle)] bg-[var(--bg-main)] px-4 py-3 text-[13.5px] leading-relaxed text-[var(--text-primary)]"
            style={{ animationDelay: "700ms" }}
          >
            <p className="stream-caret">
              In APAC, Q3 revenue grew <strong>41%</strong> YoY on the strength of
              self-serve plans, while EMEA plateaued at <strong>+6%</strong> after
              the enterprise renewal push. Simulating the proposed model at the
              observed elasticities gives a <strong>+$1.8M</strong> blended upside
              with a $340K support-cost offset.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)] px-2 py-1 text-[11px] text-[var(--text-secondary)]">
                <Globe className="h-3 w-3 text-[var(--accent)]" /> 3 sources
              </span>
              <span className="inline-flex items-center gap-1 rounded-md bg-[var(--accent-violet)]/10 px-2 py-1 text-[11px] text-[var(--accent-violet)]">
                [1] market-report-q3
              </span>
              <span className="inline-flex items-center gap-1 rounded-md bg-[var(--accent-violet)]/10 px-2 py-1 text-[11px] text-[var(--accent-violet)]">
                [2] pricing-sim.csv
              </span>
              <span className="inline-flex items-center gap-1 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)] px-2 py-1 text-[11px] text-[var(--text-secondary)]">
                <Terminal className="h-3 w-3 text-[var(--accent-cyan)]" /> ran code · 1.2s
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Hero() {
  return (
    <section id="top" className="relative overflow-hidden pt-36 pb-20 sm:pt-44">
      <div className="landing-grid absolute inset-0" aria-hidden="true" />
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: "radial-gradient(ellipse 80% 50% at 50% -15%, rgba(228, 242, 34, 0.12), rgba(99, 102, 241, 0.05) 50%, transparent 80%)",
        }}
        aria-hidden="true"
      />
      <div
        className="landing-aurora left-[-10%] top-[-20%] h-[480px] w-[480px]"
        style={{ background: "radial-gradient(circle, rgba(228, 242, 34, 0.15), transparent 65%)" }}
        aria-hidden="true"
      />
      <div
        className="landing-aurora right-[-8%] top-[5%] h-[420px] w-[420px]"
        style={{ background: "radial-gradient(circle, rgba(99, 102, 241, 0.15), transparent 65%)", animationDelay: "-6s" }}
        aria-hidden="true"
      />

      <div className="relative mx-auto max-w-4xl px-6 text-center">
        <div className="badge-pill mx-auto mb-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--status-success)] opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--status-success)]" />
          </span>
          Production-grade agentic assistant — live now
        </div>

        <h1 className="mt-7 text-[clamp(2.6rem,6.5vw,4.6rem)] font-normal leading-[1.04] tracking-[-0.03em] text-[var(--text-primary)]">
          An AI that researches, reasons, and{" "}
          <span className={GRADIENT_TEXT}>gets work done</span>
        </h1>

        <p className="mx-auto mt-6 max-w-2xl text-[17px] leading-relaxed text-[var(--text-secondary)]">
          Nexus AI pairs live web research, private document knowledge, and sandboxed
          code execution with step-by-step reasoning you can verify. Every answer is
          grounded in sources you can open.
        </p>

        <div className="mt-9 flex flex-col items-center justify-center gap-3.5 sm:flex-row">
          <Link
            href="/app"
            className="btn-electric group inline-flex w-full items-center justify-center gap-2 px-8 py-3.5 text-[14.5px] sm:w-auto"
          >
            Start chatting
            <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
          </Link>
          <a
            href="#features"
            className="btn-ghost-pill inline-flex w-full items-center justify-center gap-2 px-8 py-3.5 text-[14.5px] font-medium sm:w-auto"
          >
            See what it can do
          </a>
        </div>

        <div className="mt-8 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-[12.5px] text-[var(--text-muted)]">
          <span className="inline-flex items-center gap-1.5">
            <Check className="h-3.5 w-3.5 text-[var(--status-success)]" /> No credit card
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Check className="h-3.5 w-3.5 text-[var(--status-success)]" /> Citations on every answer
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Check className="h-3.5 w-3.5 text-[var(--status-success)]" /> Self-host friendly
          </span>
        </div>
      </div>

      <HeroMockup />
    </section>
  );
}

const FEATURES = [
  {
    icon: Globe,
    title: "Live web research",
    body: "Fresh answers backed by live web search — only when a question actually needs it, and always with cited sources.",
    accent: "text-[var(--accent)]",
  },
  {
    icon: FileText,
    title: "Private knowledge base",
    body: "Upload PDFs, Markdown, and text files. Hybrid retrieval (dense + keyword) pulls only what is relevant to your question.",
    accent: "text-[var(--accent-violet)]",
  },
  {
    icon: Terminal,
    title: "Sandboxed code execution",
    body: "Run Python in an isolated microVM and see real output instead of pseudocode. Sensitive operations pause for approval.",
    accent: "text-[var(--accent-cyan)]",
  },
  {
    icon: Brain,
    title: "Verifiable reasoning",
    body: "Expandable thinking blocks expose the chain of thought behind every answer, so you can audit the logic, not just the result.",
    accent: "text-[var(--accent-hover)]",
  },
  {
    icon: ShieldCheck,
    title: "Human-in-the-loop controls",
    body: "Destructive or risky tool calls gate on your approval mid-conversation. Nexus never acts as an uncontrollable black box.",
    accent: "text-[var(--status-success)]",
  },
  {
    icon: BarChart2,
    title: "Transparent usage",
    body: "Track tokens, per-model cost, and free-tier quota from the usage panel. Bring your own key and keep full control of spend.",
    accent: "text-[var(--status-warning)]",
  },
];

function Features() {
  return (
    <section id="features" className="relative mx-auto max-w-6xl px-6 py-24">
      <div className="max-w-2xl">
        <p className="text-[13px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
          Capabilities
        </p>
        <h2 className="mt-3 text-[clamp(1.8rem,3.5vw,2.6rem)] font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
          One assistant, <span className={GRADIENT_TEXT}>six real workflows</span>
        </h2>
        <p className="mt-4 text-[15.5px] leading-relaxed text-[var(--text-secondary)]">
          Chat, research, analyze documents, run code — the shell around every
          workflow is the same conversation, so context never has to be re-explained.
        </p>
      </div>

      <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map((f) => {
          const Icon = f.icon;
          return (
            <div
              key={f.title}
              className="glass-card group p-6 transition-colors hover:bg-[var(--bg-surface-elevated)]"
            >
              <span
                className={`inline-flex h-10 w-10 items-center justify-center rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-main)] ${f.accent}`}
              >
                <Icon className="h-5 w-5" />
              </span>
              <h3 className="mt-4 text-[16px] font-semibold text-[var(--text-primary)]">
                {f.title}
              </h3>
              <p className="mt-2 text-[14px] leading-relaxed text-[var(--text-muted)]">
                {f.body}
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
}

const PIPELINE = [
  {
    step: "01",
    title: "Plan",
    icon: MessageSquare,
    body: "The orchestrator classifies intent — question, research, or code — and builds a minimal step plan without wasting an LLM round-trip on trivia.",
  },
  {
    step: "02",
    title: "Act",
    icon: Zap,
    body: "Research runs live web or retrieval tools; code runs in a sandbox. Every tool result feeds directly back into the context.",
  },
  {
    step: "03",
    title: "Verify",
    icon: ShieldCheck,
    body: "A critic grades each claim. Weak or unsupported citations trigger deeper retrieval instead of confident guesswork.",
  },
  {
    step: "04",
    title: "Attest",
    icon: Check,
    body: "The final answer streams to your chat with visible citations, reasoning, and tool output — then persists to your history.",
  },
];

function Pipeline() {
  return (
    <section className="border-y border-[var(--border-subtle)] bg-[var(--bg-surface)]/60">
      <div className="mx-auto max-w-6xl px-6 py-24">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-[13px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
            How it works
          </p>
          <h2 className="mt-3 text-[clamp(1.8rem,3.5vw,2.6rem)] font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
            An agent pipeline you can <span className={GRADIENT_TEXT}>follow</span>
          </h2>
          <p className="mt-4 text-[15.5px] leading-relaxed text-[var(--text-secondary)]">
            No hidden magic. Each turn moves through the same auditable loop.
          </p>
        </div>

        <div className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {PIPELINE.map((p, i) => {
            const Icon = p.icon;
            return (
              <div key={p.step} className="relative">
                <div className="glass-card h-full p-6">
                  <div className="flex items-center justify-between">
                    <span className="text-[12px] font-semibold tracking-widest text-[var(--text-faint)]">
                      {p.step}
                    </span>
                    <Icon className="h-4 w-4 text-[var(--text-muted)]" />
                  </div>
                  <h3 className="mt-4 text-[16px] font-semibold text-[var(--text-primary)]">
                    {p.title}
                  </h3>
                  <p className="mt-2 text-[13.5px] leading-relaxed text-[var(--text-muted)]">
                    {p.body}
                  </p>
                </div>
                {i < PIPELINE.length - 1 && (
                  <ChevronRight className="hidden h-5 w-5 text-[var(--text-faint)] lg:absolute lg:-right-4 lg:top-1/2 lg:z-10 lg:block lg:-translate-y-1/2" />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

const MODELS = [
  {
    name: "Fast",
    model: "groq/compound-mini",
    provider: "Groq",
    latency: "~300ms",
    use: "Everyday chat and quick factual answers.",
  },
  {
    name: "Smart",
    model: "qwen/qwen3.8-27b",
    provider: "Groq",
    latency: "~2s",
    use: "Multi-step reasoning, research, and analysis.",
  },
  {
    name: "Large",
    model: "nvidia/nemotron-3:free",
    provider: "OpenRouter",
    latency: "pooled",
    use: "Long-context synthesis when the task is big.",
  },
];

function Models() {
  return (
    <section id="models" className="relative mx-auto max-w-6xl px-6 py-24">
      <div className="mx-auto max-w-2xl text-center">
        <p className="text-[13px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
          Model routing
        </p>
        <h2 className="mt-3 text-[clamp(1.8rem,3.5vw,2.6rem)] font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
          The right model for <span className={GRADIENT_TEXT}>the right job</span>
        </h2>
        <p className="mt-4 text-[15.5px] leading-relaxed text-[var(--text-secondary)]">
          Requests are routed automatically by task complexity — fast for chat,
          deep for reasoning, large for heavy context. Providers fail over
          gracefully when one is down.
        </p>
      </div>

      <div className="mt-12 grid gap-4 sm:grid-cols-3">
        {MODELS.map((m) => (
          <div key={m.name} className="glass-card p-6 text-center">
            <span className="inline-block rounded-full border border-[var(--border-subtle)] bg-[var(--bg-main)] px-3 py-1 text-[11.5px] font-medium uppercase tracking-wider text-[var(--accent-hover)]">
              {m.provider}
            </span>
            <h3 className="mt-4 text-[18px] font-semibold text-[var(--text-primary)]">
              {m.name}
            </h3>
            <code className="mt-1.5 block font-mono text-[12px] text-[var(--text-muted)]">
              {m.model}
            </code>
            <p className="mt-1 text-[12px] text-[var(--text-faint)]">{m.latency}</p>
            <p className="mt-3 text-[13.5px] leading-relaxed text-[var(--text-muted)]">
              {m.use}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}

const PLANS = [
  {
    name: "Free",
    price: "$0",
    note: "Everything live today",
    features: [
      "Chat with streaming replies",
      "Live web research with citations",
      "Private document RAG",
      "Sandboxed code execution",
      "Usage & cost tracking",
    ],
    cta: "Launch app",
    highlight: false,
  },
  {
    name: "Pro",
    price: "Soon",
    note: "For power users",
    features: [
      "Higher daily quotas",
      "More model options",
      "Longer context windows",
      "Priority routing",
    ],
    cta: "Join waitlist",
    highlight: true,
  },
  {
    name: "Team",
    price: "Soon",
    note: "For organizations",
    features: [
      "Shared knowledge bases",
      "Admin + audit controls",
      "BYOK model pools",
      "SSO & role management",
    ],
    cta: "Talk to us",
    highlight: false,
  },
];

function Pricing() {
  return (
    <section id="pricing" className="relative border-y border-[var(--border-subtle)] bg-[var(--bg-surface)]/60">
      <div className="mx-auto max-w-6xl px-6 py-24">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-[13px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
            Pricing
          </p>
          <h2 className="mt-3 text-[clamp(1.8rem,3.5vw,2.6rem)] font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
            Start free. <span className={GRADIENT_TEXT}>Scale when you need to.</span>
          </h2>
          <p className="mt-4 text-[15.5px] leading-relaxed text-[var(--text-secondary)]">
            The Free tier covers the full product today — no artificial cap on
            features, only on usage volume.
          </p>
        </div>

        <div className="mt-12 grid gap-4 lg:grid-cols-3">
          {PLANS.map((p) => (
            <div
              key={p.name}
              className={
                p.highlight
                  ? "relative rounded-xl border border-[var(--border-active)] bg-[var(--bg-surface)] p-7 shadow-float"
                  : "glass-card p-7"
              }
            >
              {p.highlight && (
                <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-[var(--accent)] px-3 py-1 text-[11px] font-semibold text-[var(--accent-foreground)] shadow-[0_0_12px_var(--accent-glow)]">
                  Popular
                </span>
              )}
              <h3 className="text-[15px] font-semibold text-[var(--text-primary)]">
                {p.name}
              </h3>
              <p className="mt-3 text-[32px] font-semibold tracking-tight text-[var(--text-primary)]">
                {p.price}
                <span className="ml-1 text-[13px] font-normal text-[var(--text-muted)]">
                  {p.note}
                </span>
              </p>
              <ul className="mt-6 space-y-3">
                {p.features.map((f) => (
                  <li key={f} className="flex items-start gap-2.5 text-[13.5px] text-[var(--text-secondary)]">
                    <Check className="mt-0.5 h-4 w-4 shrink-0 text-[var(--status-success)]" />
                    {f}
                  </li>
                ))}
              </ul>
              <Link
                href="/app"
                className={
                  p.highlight
                    ? "mt-7 btn-electric inline-flex w-full items-center justify-center px-4 py-2.5 text-[13.5px]"
                    : "mt-7 btn-ghost-pill inline-flex w-full items-center justify-center px-4 py-2.5 text-[13.5px] font-medium"
                }
              >
                {p.cta}
              </Link>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

const FAQS = [
  {
    q: "What is Nexus AI?",
    a: "Nexus AI is an agentic production assistant. Instead of a single chat model, it plans a task, runs tools like live web search, document retrieval, and sandboxed code, verifies its own claims, and then streams a cited answer into the conversation.",
  },
  {
    q: "Where do my uploaded documents go?",
    a: "Uploaded files are extracted, chunked, and embedded into a private vector index. Retrieval only ever pulls chunks relevant to your question — documents are yours, scoped to your account and conversations.",
  },
  {
    q: "How does Nexus avoid making things up?",
    a: "Every claim that depends on external facts must come from a citation retrieved or searched at runtime. A critic grades citation support, and when support looks weak, the agent goes back to research instead of guessing.",
  },
  {
    q: "Which models power the assistant?",
    a: "Nexus routes across Groq and OpenRouter models — fast and smart tiers by default. You can also bring your own API key (BYOK) and watch per-model spend in the usage panel.",
  },
];

function Faq() {
  return (
    <section id="faq" className="relative mx-auto max-w-3xl px-6 py-24">
      <div className="text-center">
        <p className="text-[13px] font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
          FAQ
        </p>
        <h2 className="mt-3 text-[clamp(1.8rem,3.5vw,2.6rem)] font-semibold tracking-[-0.02em] text-[var(--text-primary)]">
          Questions, <span className={GRADIENT_TEXT}>answered</span>
        </h2>
      </div>

      <div className="mt-12 space-y-3">
        {FAQS.map((f) => (
          <details
            key={f.q}
            className="glass-card group px-6 py-5 open:bg-[var(--bg-surface-elevated)]"
          >
            <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-[15px] font-medium text-[var(--text-primary)]">
              {f.q}
              <ChevronRight className="h-4 w-4 shrink-0 text-[var(--text-muted)] transition-transform group-open:rotate-90" />
            </summary>
            <p className="mt-3 text-[14px] leading-relaxed text-[var(--text-secondary)]">
              {f.a}
            </p>
          </details>
        ))}
      </div>
    </section>
  );
}

function FinalCta() {
  return (
    <section className="relative overflow-hidden border-t border-[var(--border-subtle)]">
      <div className="landing-grid absolute inset-0" aria-hidden="true" />
      <div
        className="landing-aurora left-1/2 top-[-60%] h-[520px] w-[560px] -translate-x-1/2"
        style={{ background: "radial-gradient(circle, rgba(45,212,191,0.18), transparent 65%)" }}
        aria-hidden="true"
      />
      <div className="relative mx-auto max-w-4xl px-6 py-28 text-center">
        <h2 className="text-[clamp(2rem,4.5vw,3.4rem)] font-semibold leading-tight tracking-[-0.025em] text-[var(--text-primary)]">
          Put your workflow on <span className={GRADIENT_TEXT}>autopilot</span>
        </h2>
        <p className="mx-auto mt-5 max-w-xl text-[16px] leading-relaxed text-[var(--text-secondary)]">
          No setup beyond a login. Ask your first question in seconds — research,
          reasoning, code, and citations, all in one thread.
        </p>
        <Link
          href="/app"
          className="btn-electric group mt-9 inline-flex items-center gap-2 px-8 py-4 text-[15.5px] shadow-[0_0_28px_var(--accent-glow)]"
        >
          Start chatting
          <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
        </Link>
      </div>
    </section>
  );
}

const FOOTER_COLS = [
  {
    title: "Product",
    links: [
      { label: "Launch app", href: "/app" },
      { label: "Features", href: "#features" },
      { label: "Model routing", href: "#models" },
      { label: "Pricing", href: "#pricing" },
    ],
  },
  {
    title: "Explore",
    links: [
      { label: "User guide", href: "#faq" },
      { label: "How it works", href: "#top" },
      { label: "FAQ", href: "#faq" },
    ],
  },
];

function Footer() {
  return (
    <footer className="border-t border-[var(--border-subtle)] bg-[var(--bg-main)]">
      <div className="mx-auto max-w-6xl px-6 py-14">
        <div className="flex flex-col justify-between gap-10 md:flex-row">
          <div className="max-w-xs">
            <div className="flex items-center gap-3">
              <BrandMark size={28} />
              <span className="text-[14px] font-semibold text-[var(--text-primary)]">
                Nexus AI
              </span>
            </div>
            <p className="mt-4 text-[13px] leading-relaxed text-[var(--text-muted)]">
              An agentic production assistant: live research, private knowledge,
              sandboxed code, and verifiable reasoning.
            </p>
          </div>

          <div className="flex gap-16">
            {FOOTER_COLS.map((col) => (
              <div key={col.title}>
                <h4 className="text-[13px] font-semibold text-[var(--text-primary)]">
                  {col.title}
                </h4>
                <ul className="mt-4 space-y-3">
                  {col.links.map((l) => (
                    <li key={l.label}>
                      <Link
                        href={l.href}
                        className="text-[13.5px] text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]"
                      >
                        {l.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>

        <div className="mt-12 flex flex-col items-center justify-between gap-3 border-t border-[var(--border-subtle)] pt-6 text-[12.5px] text-[var(--text-faint)] sm:flex-row">
          <span>© {new Date().getFullYear()} Nexus AI. All rights reserved.</span>
          <span className="inline-flex items-center gap-2">
            Built with
            <span className={GRADIENT_TEXT}>
              <Sparkles className="inline h-3.5 w-3.5" />
            </span>
            and honest citations.
          </span>
        </div>
      </div>

      <div className="flex items-center justify-center gap-2 border-t border-[var(--border-subtle)] bg-[var(--bg-surface)]/60 py-3 text-[12px] text-[var(--text-faint)]">
        <Layers className="h-3.5 w-3.5" />
        Auth-gated product at <code className="font-mono">/app</code> — sign in to start a
        conversation.
      </div>
    </footer>
  );
}

export default function Landing() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-[var(--bg-main)] text-[var(--text-primary)]">
      <Navbar />
      <Hero />
      <Features />
      <Pipeline />
      <Models />
      <Pricing />
      <Faq />
      <FinalCta />
      <Footer />
    </main>
  );
}