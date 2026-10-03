"use client";

import React, { useState, useEffect, useCallback } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { Sidebar, ConversationItem } from "@/components/Sidebar";
import {
  ChatArea,
  MessageItem,
  CitationItem,
  ToolCallItem,
  PlanReviewItem,
} from "@/components/ChatArea";
import { ChatInput, SendMessageOptions } from "@/components/ChatInput";
import { ArtifactCanvas, ArtifactItem } from "@/components/ArtifactCanvas";
import PlanHistory from "@/components/PlanHistory";
import SavedArtifacts from "@/components/SavedArtifacts";
import { CitationInspector } from "@/components/CitationInspector";
import { AuthModal } from "@/components/AuthModal";
import { CommandPalette } from "@/components/CommandPalette";
import {
  fetchConversations,
  fetchConversation,
  createConversation,
  deleteConversation,
  forkConversation,
  updateConversation,
  uploadFile,
  sendMessageFeedback,
  createPlan,
  approvePlan,
  rejectPlan,
  createArtifact,
  deleteArtifact,
  fetchArtifacts,
  fetchArtifact,
  addArtifactVersion,
  fetchPlans,
  PlanItem as APIPlanItem,
  ConversationMessage,
  CurrentUser,
  fetchCurrentUser,
} from "@/lib/api";
import { clearSession, SESSION_EXPIRED_EVENT } from "@/lib/auth";
import { useNexusChat, NexusMessage, NexusAnnotation } from "@/hooks/useNexusChat";

/**
 * The four secondary tabs are code-split.
 *
 * All of them are zero-prop components that fetch their own data on mount, so
 * splitting them is free of any wiring: nothing here has to be threaded through
 * to make the import lazy. What it buys is that none of the 2139 lines of
 * KnowledgeView/UsageView/SettingsView/AdminView are in the initial bundle.
 * AdminView alone is 1162 lines and is reachable only by an admin — the previous
 * setup shipped the entire admin console, its API helpers and its types to every
 * signed-in user, and to every signed-out visitor who could load the chunk.
 *
 * `ssr: false` is the right call for all four: each one fetches in a
 * `useEffect` on mount, so there is nothing to server-render, and skipping the
 * pass keeps their data-fetching shape out of the server render entirely. The
 * tradeoff is a brief fallback on first open, which is why the skeleton below
 * reserves the same vertical rhythm the panels use rather than collapsing to a
 * spinner in the middle of an otherwise stable layout.
 */
const TabSkeleton: React.FC<{ rows?: number }> = ({ rows = 4 }) => (
  <div
    className="mx-auto w-full max-w-3xl space-y-4 p-6"
    role="status"
    aria-live="polite"
    aria-busy="true"
  >
    <span className="sr-only">Loading…</span>
    {Array.from({ length: rows }, (_, i) => (
      <div
        key={i}
        className="h-20 animate-pulse rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)]"
        style={{ animationDelay: `${i * 70}ms` }}
      />
    ))}
  </div>
);

const KnowledgeView = dynamic(
  () => import("@/components/KnowledgeView").then((m) => m.KnowledgeView),
  { ssr: false, loading: () => <TabSkeleton rows={3} /> },
);

const UsageView = dynamic(
  () => import("@/components/UsageView").then((m) => m.UsageView),
  { ssr: false, loading: () => <TabSkeleton /> },
);

const SettingsView = dynamic(
  () => import("@/components/SettingsView").then((m) => m.SettingsView),
  { ssr: false, loading: () => <TabSkeleton rows={5} /> },
);

const AdminView = dynamic(
  () => import("@/components/AdminView").then((m) => m.AdminView),
  { ssr: false, loading: () => <TabSkeleton rows={6} /> },
);

function mapServerMessage(m: ConversationMessage): MessageItem {
  return {
    id: m.id,
    role: m.role,
    content: m.content,
    thought_process: m.thought_process ?? undefined,
    model: m.model ?? undefined,
    citations: (m.citations ?? []).map(
      (c): CitationItem => ({
        filename: c.filename ?? c.source ?? "source",
        chunk_index: c.chunk_index ?? 0,
        score: c.score ?? 0,
        content_snippet: c.content_snippet ?? c.snippet ?? "",
      })
    ),
    tool_calls: (m.tool_calls ?? []).map(
      (t): ToolCallItem => ({
        name: t.tool_name ?? t.name ?? "tool",
        args: t.tool_input ?? t.args,
        result: t.result,
        status: t.status ?? "completed",
      })
    ),
    created_at: m.created_at,
  };
}

function mapPlan(p: APIPlanItem): PlanReviewItem {
  return {
    id: p.id,
    title: p.title,
    summary: p.summary,
    steps: p.steps ?? [],
    status: p.status,
  };
}

/** Serialises the approved plan as an agent-mode system-prompt preamble. */
function buildPlanPreamble(plan: PlanReviewItem, task: string): string {
  const steps = (plan.steps ?? [])
    .map((s, i) => `${i + 1}. ${s}`)
    .join("\n");
  return [
    "APPROVED PLAN — execute these approved steps (Plan mode):",
    `Task: ${task}`,
    `Title: ${plan.title}`,
    plan.summary ? `Summary: ${plan.summary}` : "",
    "Steps:",
    steps,
    "",
    "Follow the approved steps. You may clarify details inline, but do not silently deviate from an approved step — flag any material change to the user first.",
  ]
    .filter(Boolean)
    .join("\n");
}

export default function AppPage() {
  const router = useRouter();
  const [mounted, setMounted] = useState(false);
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string>("");
  const [activeTab, setActiveTab] = useState<"chat" | "files" | "usage" | "settings" | "admin">("chat");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [currentModel, setCurrentModel] = useState("llama-3.3-70b-versatile");
  const [isAuthOpen, setIsAuthOpen] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  // Dual-Pane Artifact Canvas state
  const [activeArtifact, setActiveArtifact] = useState<ArtifactItem | null>(null);
  const [allArtifacts, setAllArtifacts] = useState<ArtifactItem[]>([]);

  // Plan mode state (draft → review → approved execution)
  const [pendingPlan, setPendingPlan] = useState<PlanReviewItem | null>(null);
  const [pendingPlanTask, setPendingPlanTask] = useState("");
  const [planBusy, setPlanBusy] = useState(false);
  // Every plan drafted for the active conversation, newest first. Distinct from
  // `pendingPlan`, which only holds the single card awaiting a decision — that
  // card is cleared on approve or reject, so without this the conversation kept
  // no record of what it had actually agreed to run, and a reload lost it
  // entirely.
  const [planHistory, setPlanHistory] = useState<APIPlanItem[]>([]);

  const handleOpenArtifact = (artifact: ArtifactItem) => {
    setActiveArtifact(artifact);
    // Add to history if not already present
    setAllArtifacts((prev) => {
      const exists = prev.some((a) => a.id === artifact.id);
      return exists ? prev : [...prev, artifact];
    });
  };

  // Grounding Citation Inspector state
  const [activeCitation, setActiveCitation] = useState<CitationItem | null>(null);

  const chat = useNexusChat({
    conversationId: activeConversationId || undefined,
    onArtifactSaved: (saved) => {
      // The stream told us a document was persisted. Fetch it rather than
      // carrying the body over SSE: the canvas needs the full content and the
      // versions list, and a 120k-char document in an event frame would be held
      // in memory twice for no benefit.
      //
      // Fire-and-forget on purpose. The reply is already on screen; if this
      // fetch fails the artifact is still in the database and still reachable
      // through the switcher, so surfacing an error here would trade a working
      // answer for a scary banner.
      void (async () => {
        try {
          const detail = await fetchArtifact(saved.artifact_id);
          handleOpenArtifact({ ...detail, isActiveVersion: true });
        } catch (err) {
          console.warn("Failed to open generated artifact:", err);
        }
      })();
    },
    onConversationCreated: (newId: string) => {
      // Backend auto-created a conversation for the first message.
      // Update state so all subsequent messages continue this same thread.
      setActiveConversationId(newId);
      setConversations((prev) => {
        if (prev.some((c) => c.id === newId)) return prev;
        const newItem: ConversationItem = {
          id: newId,
          title: "New Chat",
          updated_at: new Date().toISOString(),
          model: currentModel,
          is_pinned: false,
        };
        return [newItem, ...prev];
      });
    },
  });

  // Set mounted flag and detect real auth state on the client only.
  // This prevents the SSR/client mismatch (hydration error) caused by
  // reading cookies / localStorage during server-side rendering. The access
  // cookie is httpOnly, so auth state is verified against the backend via
  // /api/auth/me rather than read from document.cookie.
  useEffect(() => {
    // setState here runs on the first microtask after mount (inside the async
    // body, matching the react-hooks/set-state-in-effect rule) and flips the
    // hydration guard off once we know the real auth state.
    void (async () => {
      setMounted(true);
      const probe = await fetchCurrentUser();
      setIsAuthOpen(!probe.authenticated);
      setCurrentUser(probe.user ?? null);
    })();
  }, []);

  // Listen for session expiry from api.ts
  useEffect(() => {
    const onSessionExpired = () => setIsAuthOpen(true);
    window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
  }, []);

  const { setMessages, clearMessages } = chat;

  // Load conversation list and restore the most recent chat on mount
  const loadHistory = useCallback(
    async (convId: string) => {
      setHistoryLoading(true);
      try {
        const full = await fetchConversation(convId);
        const mapped = (full.messages ?? []).map(mapServerMessage);
        setMessages(
          mapped.map((m): NexusMessage => {
            // Rebuild the annotation stream from persisted fields so citations,
            // tool calls and reasoning survive a page reload.
            const annotations: NexusAnnotation[] = [];
            if (m.thought_process) {
              annotations.push({ type: "reasoning", data: { content: m.thought_process } });
            }
            for (const c of m.citations ?? []) {
              annotations.push({
                type: "citation",
                data: {
                  filename: c.filename,
                  source: c.filename,
                  content_snippet: c.content_snippet,
                  score: c.score,
                },
              });
            }
            for (const t of m.tool_calls ?? []) {
              annotations.push({
                type: "tool_call",
                data: {
                  tool_name: t.name,
                  tool_input: (t.args ?? {}) as Record<string, unknown>,
                  tool_call_id: "",
                  status: (t.status as "running" | "completed" | "error") ?? "completed",
                  result: t.result,
                },
              });
            }
            return {
              id: m.id,
              role: m.role as "user" | "assistant" | "system",
              content: m.content,
              model: m.model,
              annotations: annotations.length > 0 ? annotations : undefined,
              createdAt: m.created_at ?? new Date().toISOString(),
            };
          })
        );
      } catch (err) {
        console.warn("Failed to load conversation history:", err);
      } finally {
        setHistoryLoading(false);
      }
    },
    [setMessages]
  );

  // Load the conversation's saved artifacts.
  //
  // Without this the artifact list only ever contained what was opened in the
  // current page session: a reload produced an empty switcher even though the
  // rows were still in the database, so a saved artifact was effectively lost
  // to the user until they found the conversation that produced it. This
  // restores them the same way loadHistory restores messages, keyed on the
  // active conversation so switching chats swaps the set.
  const loadArtifacts = useCallback(async (convId: string) => {
    try {
      const list = await fetchArtifacts(convId);
      // Mark each row as the active version. The server has no such field —
      // ArtifactResponse carries only `version` — and the canvas uses the flag
      // to decide between the "Persisted version N • saved" badge and the
      // "Snapshot of version N" one. The list endpoint returns each artifact's
      // current version, so without setting it here every reloaded artifact
      // would be mislabelled as a mere snapshot of a version it actually is.
      setAllArtifacts(
        Array.isArray(list) ? list.map((a) => ({ ...a, isActiveVersion: true })) : []
      );
    } catch (err) {
      // A failure here is not fatal — the canvas still works for new artifacts,
      // it just cannot show what was saved before. Do not clear on error, so a
      // transient network error does not wipe artifacts already in state.
      console.warn("Failed to load artifacts:", err);
    }
  }, []);

  useEffect(() => {
    if (!mounted || isAuthOpen || !activeConversationId) return;
    // Fetching on a client-side id is what effects are for; the compiler lint
    // rule flags any setState reachable from an effect, which would rule out
    // loading data on mount at all.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadArtifacts(activeConversationId);
  }, [mounted, isAuthOpen, activeConversationId, loadArtifacts]);

  // Load the conversation's plan history. Without it the plan-then-approve
  // contract was write-only from the UI's point of view: plans were drafted,
  // approved and rejected, and the only trace left afterwards was the agent run
  // itself — there was no way to see which plan had been agreed to.
  const loadPlans = useCallback(async (convId: string) => {
    try {
      const list = await fetchPlans(convId);
      setPlanHistory(Array.isArray(list) ? list : []);
    } catch (err) {
      // Non-fatal: the pending-plan card and the approval flow are unaffected.
      console.warn("Failed to load plan history:", err);
    }
  }, []);

  useEffect(() => {
    if (!mounted || isAuthOpen || !activeConversationId) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- see above
    void loadPlans(activeConversationId);
  }, [mounted, isAuthOpen, activeConversationId, loadPlans]);

  // Signing out must not leave another user's artifacts or plans in memory.
  // Done during render rather than in an effect: the auth modal is rendered
  // from this same state, so clearing afterwards would paint one frame showing
  // the previous user's saved artifacts. React discards the render and retries
  // without committing when state is adjusted this way.
  const [authWasOpen, setAuthWasOpen] = useState(isAuthOpen);
  if (isAuthOpen !== authWasOpen) {
    setAuthWasOpen(isAuthOpen);
    if (isAuthOpen) {
      setAllArtifacts([]);
      setActiveArtifact(null);
      setPlanHistory([]);
    }
  }

  const handleNewChat = useCallback(() => {
    createConversation({
      title: "New Chat",
      model: currentModel,
    })
      .then((newConv) => {
        const item: ConversationItem = {
          id: newConv.id,
          title: newConv.title,
          updated_at: newConv.updated_at,
          model: newConv.model,
          is_pinned: newConv.is_pinned ?? false,
        };
        setConversations((prev) => [item, ...prev]);
        setActiveConversationId(newConv.id);
        clearMessages();
        setActiveTab("chat");
        setActiveArtifact(null);
        setActiveCitation(null);
        setPendingPlan(null);
      })
      .catch((err) => {
        console.warn("Could not create conversation:", err);
      });
  }, [currentModel, clearMessages]);

  useEffect(() => {
    // Wait until client-side auth check is done; skip if not logged in.
    if (!mounted || isAuthOpen) return;
    fetchConversations(1, 50)
      .then((res) => {
        const rawList = Array.isArray(res) ? res : (res?.items ?? []);
        const items: ConversationItem[] = rawList.map((c) => ({
          id: c.id,
          title: c.title,
          updated_at: c.updated_at,
          model: c.model,
          is_pinned: c.is_pinned ?? false,
        }));
        setConversations(items);
        if (items.length > 0) {
          setActiveConversationId(items[0].id);
          void loadHistory(items[0].id);
        } else {
          handleNewChat();
        }
      })
      .catch((err) => {
        console.warn("Backend conversation list unavailable:", err);
      });
  }, [mounted, isAuthOpen, loadHistory, handleNewChat]);

  const handleSignOut = async () => {
    // Logs out server-side: revokes the refresh token and clears both httpOnly
    // cookies (which page scripts cannot delete themselves).
    await clearSession();
    setConversations([]);
    setActiveConversationId("");
    chat.clearMessages();
    setActiveArtifact(null);
    setActiveCitation(null);
    setPendingPlan(null);
    router.replace("/");
  };

  const handleSelectConversation = (id: string) => {
    setActiveConversationId(id);
    setActiveTab("chat");
    setActiveArtifact(null);
    setActiveCitation(null);
    setPendingPlan(null);
    void loadHistory(id);
  };

  const handleDeleteConversation = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteConversation(id);
    } catch {
      // Ignored
    }
    setConversations((prev) => prev.filter((c) => c.id !== id));
    if (activeConversationId === id) {
      const remaining = conversations.filter((c) => c.id !== id);
      if (remaining.length > 0) {
        setActiveConversationId(remaining[0].id);
        void loadHistory(remaining[0].id);
      } else {
        handleNewChat();
      }
    }
  };

  const handleRenameConversation = async (id: string, title: string) => {
    const previous = conversations.find((c) => c.id === id)?.title;
    // Optimistic: the sidebar re-renders instantly and the server call
    // follows. On failure the row snaps back to the title it had before.
    setConversations((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title } : c)),
    );
    try {
      await updateConversation(id, { title });
    } catch (err) {
      if (previous !== undefined) {
        setConversations((prev) =>
          prev.map((c) => (c.id === id ? { ...c, title: previous } : c)),
        );
      }
      console.warn("Rename conversation failed:", err);
      throw err;
    }
  };

  const handleForkConversation = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const currentMsgs = chat.messages;
    if (!currentMsgs.length) return;
    const lastMsgId = currentMsgs[currentMsgs.length - 1].id;
    try {
      const forked = await forkConversation(id, lastMsgId, "Forked Branch");
      const item: ConversationItem = {
        id: forked.id,
        title: forked.title,
        updated_at: forked.updated_at,
        model: forked.model,
        is_pinned: forked.is_pinned ?? false,
      };
      setConversations((prev) => [item, ...prev]);
      setActiveConversationId(forked.id);
      void loadHistory(forked.id);
    } catch (err) {
      console.warn("Fork conversation failed:", err);
    }
  };

  const handleStop = () => {
    chat.stop();
  };

  const handleDraftPlan = async (convId: string, content: string) => {
    setPlanBusy(true);
    setPendingPlanTask(content.trim());
    try {
      const plan = await createPlan(convId, content.trim());
      setPendingPlan(mapPlan(plan));
      // Pull the new draft into history immediately, so the list reflects the
      // pending decision without waiting for a reload.
      void loadPlans(convId);
    } catch (err) {
      console.warn("Plan draft failed:", err);
    } finally {
      setPlanBusy(false);
    }
  };

  const handleApprovePlan = async (plan: PlanReviewItem) => {
    if (planBusy) return;
    setPlanBusy(true);
    try {
      await approvePlan(plan.id);
      setPendingPlan(null);
      const convId = activeConversationId;
      // Re-read so the history row flips to "approved" rather than staying
      // "pending" until the conversation is reloaded.
      if (convId) void loadPlans(convId);
      if (convId && pendingPlanTask) {
        await chat.sendMessage(pendingPlanTask, {
          conversationId: convId,
          mode: "agent",
          planPreamble: buildPlanPreamble(plan, pendingPlanTask),
        });
      }
    } catch (err) {
      console.warn("Plan approval failed:", err);
    } finally {
      setPlanBusy(false);
      // Consume the task. It is set *before* the draft request, so a failed
      // draft left it populated; and it was never cleared at all, so approving
      // any later plan card re-sent the previous task as a brand-new agent
      // message. Clear it on every exit path -- on success it has been consumed,
      // and on failure replaying a stale task is worse than doing nothing.
      setPendingPlanTask("");
    }
  };

  const handleRejectPlan = async (plan: PlanReviewItem) => {
    if (planBusy) return;
    setPlanBusy(true);
    try {
      await rejectPlan(plan.id, "Rejected in UI");
      setPendingPlan(null);
      if (activeConversationId) void loadPlans(activeConversationId);
    } catch (err) {
      console.warn("Plan rejection failed:", err);
    } finally {
      setPlanBusy(false);
      // A rejected plan must not leave its originating task armed for the next
      // approval.
      setPendingPlanTask("");
    }
  };

  const handleDismissPlan = () => {
    if (planBusy) return;
    setPendingPlan(null);
    setPendingPlanTask("");
  };

  /**
   * Saves artifact content, creating the row on a first save and appending a
   * version once it already exists.
   *
   * The two cases must stay distinct: an unconditional createArtifact would
   * POST to /artifacts and leave a second row for the same document rather
   * than recording a new version of the existing one.
   */
  const handleSaveArtifact = async (artifact: ArtifactItem) => {
    const isPersisted = artifact.version !== undefined;
    try {
      const saved = isPersisted
        ? await addArtifactVersion(artifact.id, artifact.content, {
            title: artifact.title,
            language: artifact.language,
          })
        : await createArtifact({
            title: artifact.title,
            content: artifact.content,
            language: artifact.language,
            conversation_id: activeConversationId || null,
          });
      const persisted: ArtifactItem = {
        ...artifact,
        id: saved.id,
        version: saved.version,
        isActiveVersion: true,
      };
      setAllArtifacts((prev) =>
        isPersisted
          ? prev.map((a) => (a.id === artifact.id ? persisted : a))
          : [...prev.filter((a) => a.id !== artifact.id), persisted]
      );
      setActiveArtifact((prev) =>
        prev && prev.id === artifact.id ? persisted : prev
      );
    } catch (err) {
      console.warn("Artifact save failed:", err);
      // Rethrown so the canvas can keep the edit buffer and show the failure
      // rather than closing the editor as if the save had worked.
      throw err;
    }
  };

  const handleDeleteArtifact = async (artifact: ArtifactItem) => {
    if (artifact.version === undefined) return; // transient canvas item
    try {
      await deleteArtifact(artifact.id);
      setAllArtifacts((prev) => prev.filter((a) => a.id !== artifact.id));
      setActiveArtifact((prev) => (prev && prev.id === artifact.id ? null : prev));
    } catch (err) {
      console.warn("Artifact delete failed:", err);
    }
  };

  const handleSendMessage = async (
    content: string,
    options: SendMessageOptions
  ) => {
    // Plan mode: draft an actionable plan first — execution only after approval.
    if (options.planMode && content.trim()) {
      let convId = activeConversationId;
      if (!convId) {
        try {
          const conv = await createConversation({
            title: content.trim().slice(0, 120),
            model: currentModel,
          });
          convId = conv.id;
          setActiveConversationId(convId);
          setConversations((prev) => {
            if (prev.some((c) => c.id === convId)) return prev;
            const item: ConversationItem = {
              id: convId,
              title: conv.title,
              updated_at: conv.updated_at,
              model: conv.model,
              is_pinned: false,
            };
            return [item, ...prev];
          });
        } catch (err) {
          console.warn("Conversation creation for plan mode failed:", err);
          return;
        }
      }
      await handleDraftPlan(convId, content);
      return;
    }

    // Upload any attachments first
    if (options.attachments && options.attachments.length > 0) {
      for (const file of options.attachments) {
        try {
          await uploadFile(file);
        } catch (uploadErr) {
          console.warn("Attachment upload warning:", uploadErr);
        }
      }
    }

    const mode = options.enableCode
      ? "code"
      : options.enableWeb
      ? "research"
      : options.agentMode === "deep"
      ? "agent"
      : "normal";

    void chat.sendMessage(content, { mode, imageDataUrl: options.imageDataUrl });
  };

  const messages: MessageItem[] = chat.messages.map((m: NexusMessage) => {
    const thoughts = chat.getReasoningBlocks(m).map((a) => a.data.content).join("\n");
    const citations: CitationItem[] = chat.getCitations(m).map((a) => ({
      filename: a.data.filename ?? a.data.source ?? a.data.url ?? "source",
      chunk_index: (a.data as { chunk_index?: number }).chunk_index ?? 0,
      score: a.data.score ?? 0,
      content_snippet: a.data.content_snippet ?? a.data.snippet ?? "",
    }));
    const toolCalls: ToolCallItem[] = chat.getToolCalls(m).map((a) => ({
      name: a.data.tool_name,
      args: a.data.tool_input,
      result: a.data.result,
      status: a.data.status ?? "running",
    }));
    return {
      id: m.id,
      role: m.role,
      content: m.content,
      model: m.model,
      thought_process: thoughts || undefined,
      citations: citations.length > 0 ? citations : undefined,
      tool_calls: toolCalls.length > 0 ? toolCalls : undefined,
      created_at: typeof m.createdAt === "string" ? m.createdAt : undefined,
      imageDataUrl: m.imageDataUrl,
    };
  });

  const isAdmin = currentUser?.role === "admin";

  // Non-admins can't see the Admin tab in the sidebar, but if activeTab is ever
  // "admin" without an admin role the tab is derived away at render time — no
  // effect round-trip needed. The backend /api/v1/admin/* check is the real gate.
  const effectiveTab = activeTab === "admin" && !isAdmin ? "chat" : activeTab;

  const handleFeedback = async (messageId: string, feedback: "thumbs_up" | "thumbs_down") => {
    if (!activeConversationId) return;
    try {
      await sendMessageFeedback(activeConversationId, messageId, feedback);
    } catch (err) {
      console.warn("Feedback submission failed:", err);
    }
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[var(--bg-main)] text-[var(--text-primary)]">
      {/* Sidebar Navigation */}
      <Sidebar
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelectConversation={handleSelectConversation}
        onNewChat={handleNewChat}
        onDeleteConversation={handleDeleteConversation}
        onForkConversation={handleForkConversation}
        onRenameConversation={handleRenameConversation}
        activeTab={effectiveTab}
        setActiveTab={setActiveTab}
        currentModel={currentModel}
        onChangeModel={setCurrentModel}
        onSignOut={handleSignOut}
        showAdmin={isAdmin}
        onOpenCommandPalette={() => setPaletteOpen(true)}
      />

      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        conversations={conversations}
        onSelectConversation={handleSelectConversation}
        onNewChat={handleNewChat}
        setActiveTab={setActiveTab}
        onChangeModel={setCurrentModel}
        currentModel={currentModel}
        onSignOut={handleSignOut}
        showAdmin={isAdmin}
      />

      <AuthModal
        isOpen={isAuthOpen}
        onClose={() => {
          clearSession();
          setIsAuthOpen(false);
        }}
        onSuccess={() => setIsAuthOpen(false)}
      />

      {/* Main Content Area */}
      <main className="flex-1 flex flex-col h-full overflow-hidden relative">
        {effectiveTab === "chat" && (
          <div className="flex-1 flex h-full overflow-hidden relative">
            {/* Chat Column */}
            <div
              className={`flex flex-col h-full overflow-hidden transition-all duration-200 ${
                activeArtifact ? "w-full md:w-[52%]" : "w-full"
              }`}
            >
              {historyLoading ? (
                <div className="flex-1 flex items-center justify-center text-sm text-[var(--text-muted)]">
                  Loading conversation…
                </div>
              ) : (
                <ChatArea
                  messages={messages}
                  isLoading={chat.isLoading}
                  error={chat.error?.message ?? null}
                  onRetry={chat.error ? chat.reload : undefined}
                  pendingHITL={
                    chat.pendingHITL
                      ? {
                          thread_id: chat.pendingHITL.thread_id,
                          request: chat.pendingHITL.request,
                          plan: chat.pendingHITL.plan,
                          tool_name: chat.pendingHITL.tool_name,
                          arguments: chat.pendingHITL.arguments,
                        }
                      : null
                  }
                  onResolveHITL={chat.resolveHITL}
                  turnVerdict={chat.turnVerdict}
                  onFeedback={handleFeedback}
                  onOpenArtifact={handleOpenArtifact}
                  onSelectCitation={setActiveCitation}
                  onSaveArtifact={handleSaveArtifact}
                  pendingPlan={pendingPlan}
                  onApprovePlan={handleApprovePlan}
                  onRejectPlan={handleRejectPlan}
                  onDismissPlan={handleDismissPlan}
                  planBusy={planBusy}
                />
              )}
              <SavedArtifacts artifacts={allArtifacts} onOpen={handleOpenArtifact} />
              <PlanHistory plans={planHistory} />
              <ChatInput
                onSendMessage={handleSendMessage}
                isLoading={chat.isLoading}
                onStop={handleStop}
                queuedMessages={chat.queuedMessages}
                onCancelQueued={chat.cancelQueued}
              />
            </div>
            {/* Dual-Pane Artifact Canvas */}
            {activeArtifact && (
              <ArtifactCanvas
                artifact={activeArtifact}
                artifacts={allArtifacts}
                onClose={() => setActiveArtifact(null)}
                onSelectArtifact={(art) => setActiveArtifact(art)}
                onDeleteArtifact={handleDeleteArtifact}
                onSaveVersion={async (art, content) => {
                  await handleSaveArtifact({ ...art, content });
                }}
              />
            )}

            {/* Grounding Source Citation Inspector */}
            <CitationInspector
              citation={activeCitation}
              onClose={() => setActiveCitation(null)}
            />
          </div>
        )}

        {effectiveTab === "files" && (
  <KnowledgeView />
)}
        {effectiveTab === "usage" && <UsageView />}
        {effectiveTab === "settings" && <SettingsView />}
        {effectiveTab === "admin" && <AdminView />}
      </main>
    </div>
  );
}