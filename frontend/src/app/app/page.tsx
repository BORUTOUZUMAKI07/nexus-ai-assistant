"use client";

import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { Sidebar } from "@/components/Sidebar";
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
import { Menu, Moon, Sun, Sliders } from "lucide-react";
import { toast } from "sonner";
import { CommandPalette } from "@/components/CommandPalette";
import { KeyboardShortcutsGuide } from "@/components/KeyboardShortcuts";
import { ModelComparison } from "@/components/ModelComparison";
import {
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
  fetchArtifact,
  addArtifactVersion,
  createShareLink,
  revokeShareLinks,
  PlanItem as APIPlanItem,
  Conversation,
  CurrentUser,
  fetchCurrentUser,
} from "@/lib/api";
import { clearSession, SESSION_EXPIRED_EVENT } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { createQueryClient } from "@/lib/query-client";
import {
  useConversations,
  useArtifacts,
  usePlans,
  useSettingsQuery,
  conversationKeys,
  artifactKeys,
  planKeys,
  type CachedArtifact,
} from "@/lib/queries";
import { useNexusChat, NexusMessage } from "@/hooks/useNexusChat";
import { DEFAULT_MODEL_ID, canonicalModelId } from "@/lib/models";
import { resolveConversationMode } from "@/lib/chatMode";

/**
 * The five secondary tabs are code-split.
 *
 * All of them fetch their own data on mount, so splitting is nearly free of
 * wiring: nothing here has to be threaded through to make the import lazy.
 * What it buys is that none of the 2139 lines of
 * KnowledgeView/UsageView/SettingsView/AdminView are in the initial bundle.
 * AdminView alone is 1162 lines and is reachable only by an admin — the previous
 * setup shipped the entire admin console, its API helpers and its types to every
 * signed-in user, and to every signed-out visitor who could load the chunk.
 * ProjectsView is the one exception: it takes a single `onOpenConversation`
 * callback so a project member can be opened in the chat tab.
 *
 * `ssr: false` is the right call for all five: each one fetches in a
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

const ProjectsView = dynamic(
  () => import("@/components/ProjectsView").then((m) => m.ProjectsView),
  { ssr: false, loading: () => <TabSkeleton rows={3} /> },
);

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

/**
 * Provides the query client, then renders the shell.
 *
 * The client has to be created and provided *above* everything that reads it,
 * which is why the shell is split out: a component cannot call `useQuery` in its
 * own body and also be the one that provides the client. One client is created
 * per page instance (lazy initializer) so a re-render never swaps it out from
 * under the queries it is managing.
 */
export default function AppPage() {
  const [queryClient] = useState(() => createQueryClient());
  return (
    <QueryClientProvider client={queryClient}>
      <AppPageInner />
    </QueryClientProvider>
  );
}

function AppPageInner() {
  const queryClient = useQueryClient();
  const router = useRouter();
  const { mode, setMode, resolved } = useTheme();
  const [mounted, setMounted] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [comparisonQuestion, setComparisonQuestion] = useState("");
  const [activeConversationId, setActiveConversationId] = useState<string>("");
  const [activeTab, setActiveTab] = useState<"chat" | "files" | "projects" | "usage" | "settings" | "admin">("chat");
  const [paletteOpen, setPaletteOpen] = useState(false);
  // The sidebar is a 288px column on desktop and an overlay drawer below `md`.
  // One boolean drives both: the wrapper is always rendered (so the desktop
  // layout is unchanged) and only translates off-canvas under `md`.
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const closeSidebar = useCallback(() => setSidebarOpen(false), []);
  // The model the user (or the command palette) picked in this session, or null
  // while they have not picked one. `currentModel` below is *derived* from this
  // plus the saved default: it used to be a second piece of state written from
  // an effect, which meant a cascading render on every mount and two sources of
  // truth for one string.
  const [pickedModel, setPickedModel] = useState<string | null>(null);
  const [isAuthOpen, setIsAuthOpen] = useState(false);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  // A conversation's messages are fetched by the chat hook, not here. See the
  // note below: two loaders writing one transcript is the race that ordering
  // was introduced to remove.

  // Dual-Pane Artifact Canvas state
  const [activeArtifact, setActiveArtifact] = useState<ArtifactItem | null>(null);
  // Artifacts opened in *this* page session that are not in the saved list:
  // transient canvas items and rows the stream just produced. Saved artifacts
  // come from the query cache; `allArtifacts` below merges the two, saved first.
  const [sessionArtifacts, setSessionArtifacts] = useState<ArtifactItem[]>([]);

  // Plan mode state (draft → review → approved execution)
  const [pendingPlan, setPendingPlan] = useState<PlanReviewItem | null>(null);
  // Bumped on "New conversation" and passed as ChatInput's `key`: the composer's
  // mode toggles (Web/Code/Plan) live in its own state, so without a remount a
  // toggle lit for the previous conversation stayed lit here while quietly
  // shaping -- or being overridden in -- the next one.
  const [composerEpoch, setComposerEpoch] = useState(0);
  const [pendingPlanTask, setPendingPlanTask] = useState("");
  const [planBusy, setPlanBusy] = useState(false);
  // Every plan drafted for the active conversation, newest first. Distinct from
  // `pendingPlan`, which only holds the single card awaiting a decision — that
  // card is cleared on approve or reject, so without this the conversation kept
  // no record of what it had actually agreed to run, and a reload lost it
  // entirely. Sourced from the plans query below.

  const handleOpenArtifact = (artifact: ArtifactItem) => {
    // A reply's artifact card carries the artifact as *extracted* from the answer
    // text, which has no server id. After the user saves it, that card still
    // holds the transient copy — so opening from the card produced a canvas with
    // no version, no history and no Edit button, even though the document was
    // persisted a second earlier. Prefer the saved row when it describes the same
    // document.
    const persistedTwin =
      artifact.version === undefined
        ? allArtifacts.find(
            (a) =>
              a.version !== undefined &&
              a.content === artifact.content &&
              a.title === artifact.title &&
              a.language === artifact.language
          )
        : undefined;
    const opened = persistedTwin
      ? { ...persistedTwin, isActiveVersion: true }
      : artifact;

    setActiveArtifact(opened);
    // Add to the session list if it is not already there. A saved artifact is
    // already in the query cache and shows through the merge, so a duplicate
    // here is harmless — the merge dedupes by id, saved first.
    setSessionArtifacts((prev) =>
      prev.some((a) => a.id === opened.id) ? prev : [...prev, opened]
    );
  };

  // Grounding Citation Inspector state
  const [activeCitation, setActiveCitation] = useState<CitationItem | null>(null);

  // ── Server state ──────────────────────────────────────────────────────────
  // The conversation list, the active conversation's saved artifacts and its
  // plan history are server state, so they live on the query client rather than
  // in hand-rolled `useState` + fetch-on-mount effects. The chat stream is
  // deliberately *not* here: `useNexusChat` remains the transcript's only writer.
  const conversationsQuery = useConversations(mounted && !isAuthOpen);
  const conversations = conversationsQuery.data ?? [];

  const artifactsQuery = useArtifacts(activeConversationId, mounted && !isAuthOpen);
  // Wrapped so the `?? []` fallback is referentially stable while data is
  // undefined; a fresh array each render would invalidate `allArtifacts` and
  // (more visibly) churn every consumer of it on no actual change.
  const savedArtifacts = useMemo(() => artifactsQuery.data ?? [], [artifactsQuery.data]);
  const savedArtifactIds = useMemo(
    () => new Set(savedArtifacts.map((a) => a.id)),
    [savedArtifacts],
  );
  // Saved first, then this session's unsaved ones. Deliberately not wrapped in
  // `useMemo`: the React Compiler is enabled (`next.config.ts` `reactCompiler`),
  // and it refuses to compile the whole component when a hand-written memo's
  // dependencies are not the ones it infers — which was a hard lint error. It
  // memoises this value itself, from the same inputs, once it can compile.
  const allArtifacts = [
    ...savedArtifacts,
    ...sessionArtifacts.filter((a) => !savedArtifactIds.has(a.id)),
  ];

  const plansQuery = usePlans(activeConversationId, mounted && !isAuthOpen);
  const planHistory = plansQuery.data ?? [];

  // The settings page saves a "Default model", and nothing read it: the picker
  // always started at DEFAULT_MODEL_ID, so changing the default changed nothing.
  // Apply it once, on hydration, and only while the user has not picked a model
  // themselves. A legacy stored slug is canonicalised to its router group.
  const settingsQuery = useSettingsQuery(mounted && !isAuthOpen);
  // A deliberate choice this session always wins over the saved default, and the
  // default only exists once the settings query resolves — both are properties
  // of the current render, so this is a derivation. A legacy stored slug is
  // canonicalised to its router group.
  const currentModel =
    pickedModel ?? canonicalModelId(settingsQuery.data?.default_model) ?? DEFAULT_MODEL_ID;

  const chooseModel = useCallback((model: string) => setPickedModel(model), []);

  const chat = useNexusChat({
    conversationId: activeConversationId || undefined,
    // The picker's selection has to reach the hook *here*. It was previously
    // never passed, so `optionsRef.current.model` was always `undefined`: the
    // picker moved a string that was dropped before the request left the
    // browser, and the backend chose the model regardless of what was shown.
    model: currentModel,
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
      // Update the cache so all subsequent messages continue this same thread.
      setActiveConversationId(newId);
      queryClient.setQueryData<Conversation[]>(conversationKeys.all, (prev) => {
        if (prev?.some((c) => c.id === newId)) return prev;
        const newItem: Conversation = {
          id: newId,
          title: "New Chat",
          model: currentModel,
          is_pinned: false,
          is_archived: false,
          project_id: null,
          token_count: 0,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        return [newItem, ...(prev ?? [])];
      });
    },
  });

  const { clearMessages } = chat;

  // Set mounted flag and detect real auth state on the client only.
  // This prevents the SSR/client mismatch (hydration error) caused by
  // reading cookies / localStorage during server-side rendering. The access
  // cookie is httpOnly, so auth state is verified against the backend via
  // /api/auth/me rather than read from document.cookie.
  useEffect(() => {
    // setState here runs on the first microtask after mount (inside the async
    // body, matching the react-hooks/set-state-in-effect rule) and flips the
    // hydration guard off once we know the real auth state.
    let cancelled = false;
    void (async () => {
      setMounted(true);
      // A probe that could not reach the backend is `unavailable`, not
      // "signed out": treating it as the latter replaced a live session with
      // the sign-in modal during a backend cold start (measured: /api/auth/me
      // answered `authenticated: false` while the cookie was still valid, and
      // the app stayed signed out until a manual reload). Retry instead, and
      // if the backend never answers, keep the workspace rather than a login
      // form the user cannot use anyway.
      const backoffMs = [400, 1200, 3000, 6000, 10000];
      for (let attempt = 0; !cancelled; attempt += 1) {
        const probe = await fetchCurrentUser();
        if (cancelled) return;
        if (!probe.unavailable) {
          setIsAuthOpen(!probe.authenticated);
          setCurrentUser(probe.user ?? null);
          return;
        }
        if (attempt >= backoffMs.length) {
          setIsAuthOpen(false);
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, backoffMs[attempt]));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // `?` toggles the keyboard shortcuts guide. Esc closes it.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "?") {
        e.preventDefault();
        setShowShortcuts((s) => !s);
      }
      if (e.key === "Escape" && showShortcuts) {
        setShowShortcuts(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [showShortcuts]);

  // Listen for session expiry from api.ts. This is the only path into the auth
  // modal that happens mid-session (the mount path opens it with an empty
  // cache), so it is where the previous user's cached lists are dropped.
  useEffect(() => {
    const onSessionExpired = () => {
      queryClient.clear();
      setSessionArtifacts([]);
      setActiveArtifact(null);
      setIsAuthOpen(true);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
  }, [queryClient]);

  // Conversation history is loaded by the chat hook, deliberately, and this loader
  // was removed rather than kept alongside it.
  //
  // It was not a duplicate by accident: both wrote the *whole* message list, and
  // selecting a conversation started both. Whichever resolved last won, so a
  // history request that came back after the rejoin had streamed frames replaced
  // the transcript and made a recovered answer vanish mid-stream. Fixing the
  // rejoin without removing this would have documented a guarantee the code did
  // not have -- so the single writer is the hook, which also owns the ordering
  // (history, then rejoin) and the duplicate-answer check.
  //
  // `chat.isHydrating` replaces the `historyLoading` flag this kept.

  // Session-opened artifacts belong to the conversation they were opened in.
  // The saved list is keyed by conversation and swaps on its own; this drops the
  // transient/session half so it cannot leak into the next conversation.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSessionArtifacts([]);
  }, [activeConversationId]);

  const handleNewChat = useCallback(() => {
    // Fresh conversation = fresh composer intent; never carry Web/Code/Plan
    // toggles over from the conversation the user just left.
    setComposerEpoch((n) => n + 1);
    createConversation({
      title: "New Chat",
      model: currentModel,
    })
      .then((newConv) => {
        queryClient.setQueryData<Conversation[]>(conversationKeys.all, (prev) => {
          if (prev?.some((c) => c.id === newConv.id)) return prev;
          return [newConv, ...(prev ?? [])];
        });
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
  }, [currentModel, clearMessages, queryClient, setActiveCitation]);

  // Seed the active conversation once the list has actually loaded: select the
  // most recent, or create a first one when the account genuinely has none.
  //
  // `hasSeededRef` makes this a one-shot. Without it every cache write (a
  // rename, a new chat, the optimistic edits elsewhere) would re-run the
  // selection and fight the user for control of `activeConversationId`. It is
  // re-armed when the auth modal opens so signing back in seeds again.
  const hasSeededRef = useRef(false);
  useEffect(() => {
    if (!mounted || isAuthOpen) return;
    if (!conversationsQuery.isSuccess) return;
    if (hasSeededRef.current) return;
    hasSeededRef.current = true;
    const items = conversationsQuery.data ?? [];
    if (items.length > 0) {
      // Syncing loaded server state into the shell's selected id is the effect's
      // job here; it runs at most once (`hasSeededRef`) and is why the
      // transcript has an owner at all rather than racing two loaders.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setActiveConversationId(items[0].id);
    } else {
      handleNewChat();
    }
  }, [
    mounted,
    isAuthOpen,
    conversationsQuery.isSuccess,
    conversationsQuery.data,
    handleNewChat,
  ]);

  useEffect(() => {
    if (isAuthOpen) hasSeededRef.current = false;
  }, [isAuthOpen]);

  const handleSignOut = async () => {
    // Logs out server-side: revokes the refresh token and clears both httpOnly
    // cookies (which page scripts cannot delete themselves). Awaiting it is
    // load-bearing — navigating before the Set-Cookie lands leaves the edge gate
    // looking at a live cookie, which is the bounce the url-shortner's sidebar
    // documents in detail.
    const cleared = await clearSession();
    // One clear drops the conversation, artifact and plan caches together; the
    // session list is separate state and is emptied explicitly.
    queryClient.clear();
    setSessionArtifacts([]);
    setActiveConversationId("");
    chat.clearMessages();
    setActiveArtifact(null);
    setActiveCitation(null);
    setPendingPlan(null);
    // A full navigation to /signin, not router.replace("/"):
    //
    // - the landing page is not where a sign-out ends, the sign-in page is;
    // - `router.replace` keeps this tree mounted for a tick, and the cache clear
    //   above makes every query refetch with the cookies already gone. Those 401s
    //   fire SESSION_EXPIRED_EVENT, which opens the in-app sign-in *dialog* — the
    //   small sign-in card that appeared over the workspace after signing out.
    //   A real page load tears the tree down before any of that can run.
    //
    // If the cookies were NOT cleared (the logout request never reached the BFF),
    // `?expired=1` is what the edge gate honours to let the form through instead
    // of bouncing straight back into the workspace.
    window.location.assign(cleared ? "/signin" : "/signin?expired=1");
  };

  const handleSelectConversation = (id: string) => {
    setActiveConversationId(id);
    setActiveTab("chat");
    setActiveArtifact(null);
    setActiveCitation(null);
    setPendingPlan(null);
  };

  const handlePinConversation = useCallback(async (id: string, pinned: boolean) => {
    const previous = conversations.find((c) => c.id === id)?.is_pinned;
    queryClient.setQueryData<Conversation[]>(conversationKeys.all, (prev) =>
      (prev ?? []).map((c) => (c.id === id ? { ...c, is_pinned: pinned } : c)),
    );
    try {
      await updateConversation(id, { is_pinned: pinned });
    } catch (err) {
      if (previous !== undefined) {
        queryClient.setQueryData<Conversation[]>(conversationKeys.all, (prev) =>
          (prev ?? []).map((c) => (c.id === id ? { ...c, is_pinned: previous } : c)),
        );
      }
      console.warn("Pin conversation failed:", err);
      throw err;
    }
  }, [conversations, queryClient]);

  const autoTitleDoneRef = useRef<Set<string>>(new Set());

  const handleShareConversation = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      // Create a fresh read-only link and put it on the clipboard in one click;
      // revocation lives on the same toast so a link handed out by mistake can
      // be pulled back without leaving the conversation.
      const link = await createShareLink(id);
      try {
        await navigator.clipboard.writeText(link.url);
      } catch {
        // Clipboard access can be denied; the link still exists, so the toast
        // below still tells the user where it is.
      }
      toast.success("Share link copied", {
        description: link.expires_at
          ? `Read-only, expires ${new Date(link.expires_at).toLocaleString()}`
          : "Read-only — anyone with the link can view this conversation",
        action: {
          label: "Revoke",
          onClick: () => {
            // Access is cut for everyone holding the link, with no undo beyond
            // sharing again — so it asks, like every other destructive control.
            if (!window.confirm("Revoke this share link? Anyone using it loses access immediately.")) return;
            void revokeShareLinks(id)
              .then(() => toast.success("Share link revoked"))
              .catch((err) =>
                toast.error(
                  err instanceof Error ? err.message : "Could not revoke the link",
                ),
              );
          },
        },
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not share conversation");
    }
  };

  const handleDeleteConversation = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    // Asked before anything is touched, and after stopPropagation so a cancelled
    // confirm does not also select the row. Same rule, and the same native prompt,
    // as the url-shortner's destructive actions.
    if (!window.confirm("Delete this conversation? Its messages and artifacts are removed permanently.")) {
      return;
    }
    try {
      await deleteConversation(id);
      // Remove from the sidebar only after the server confirms the delete.
      // The previous version removed it unconditionally, so a network error
      // silently lost the item from the UI while it still existed on the server.
      const remaining = conversations.filter((c) => c.id !== id);
      queryClient.setQueryData<Conversation[]>(conversationKeys.all, remaining);
      if (activeConversationId === id) {
        if (remaining.length > 0) {
          setActiveConversationId(remaining[0].id);
        } else {
          handleNewChat();
        }
      }
    } catch (err) {
      console.warn("Failed to delete conversation:", err);
    }
  };

  const handleRenameConversation = async (id: string, title: string) => {
    const previous = conversations.find((c) => c.id === id)?.title;
    // Optimistic: the sidebar re-renders instantly and the server call
    // follows. On failure the row snaps back to the title it had before.
    queryClient.setQueryData<Conversation[]>(conversationKeys.all, (prev) =>
      (prev ?? []).map((c) => (c.id === id ? { ...c, title } : c)),
    );
    try {
      await updateConversation(id, { title });
    } catch (err) {
      if (previous !== undefined) {
        queryClient.setQueryData<Conversation[]>(conversationKeys.all, (prev) =>
          (prev ?? []).map((c) => (c.id === id ? { ...c, title: previous } : c)),
        );
      }
      console.warn("Rename conversation failed:", err);
      throw err;
    }
  };

  useEffect(() => {
    if (!activeConversationId || chat.isLoading || !chat.messages || chat.messages.length < 2) return;
    if (autoTitleDoneRef.current.has(activeConversationId)) return;
    const known = conversations.find((c) => c.id === activeConversationId);
    // A conversation we cannot find is *unknown*, not untitled. Treating the
    // two the same renamed anything whose row had not reached this list yet —
    // a freshly branched fork, whose server-assigned `"(Branch: …)"` title was
    // replaced by its first user message. Every path that creates a
    // conversation for the first time (`handleNewChat`, `onConversationCreated`,
    // `handleBranchMessage`) inserts it into this list before selecting it, so
    // a miss here means "not loaded yet" and the correct action is to wait.
    if (!known) return;
    const looksDefault =
      !known.title || known.title === "New Chat" || known.title === "New Conversation" || known.title === "Untitled";
    if (!looksDefault) {
      autoTitleDoneRef.current.add(activeConversationId);
      return;
    }
    const firstUser = chat.messages.find((m) => m.role === "user");
    const raw = (firstUser?.content ?? "").replace(/\s+/g, " ").trim();
    if (!raw) return;
    const title = raw.slice(0, 60).trim() + (raw.length > 60 ? "…" : "");
    autoTitleDoneRef.current.add(activeConversationId);
    handleRenameConversation(activeConversationId, title).catch(() => {});
  }, [chat.messages, chat.isLoading, activeConversationId, conversations, chat]);

  const handleForkConversation = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const currentMsgs = chat.messages;
    if (!currentMsgs.length) return;
    // The last message's id may be a client-side `msg-<ts>` id rather than a
    // server UUID (live turns are not reconciled until a reload), so
    // `forkConversation` drops it unless it is a real server id and lets the
    // backend fork at the latest persisted message.
    const lastMsgId = currentMsgs[currentMsgs.length - 1].id;
    try {
      const forked = await forkConversation(id, lastMsgId, "Forked Branch");
      queryClient.setQueryData<Conversation[]>(conversationKeys.all, (prev) => [
        forked,
        ...(prev ?? []),
      ]);
      setActiveConversationId(forked.id);
    } catch (err) {
      console.warn("Fork conversation failed:", err);
    }
  };

  const handleStop = () => {
    chat.stop();
  };

  /**
   * Branch from a specific message.
   *
   * Distinct from `handleForkConversation` (which forks at the transcript's
   * *end*, from the overflow menu): here the user points at the turn they want
   * to branch from, so the id is passed through explicitly. Branching is a
   * non-destructive action — it creates a new conversation and leaves this one
   * untouched — so there is nothing to confirm.
   *
   * The fork is inserted into the list *before* it becomes active, exactly like
   * `handleNewChat` and `onConversationCreated`. That is not cosmetic: the
   * auto-titler renames the active conversation when it cannot find a title for
   * it, and a fork the list has never heard of was therefore renamed to its
   * first user message — replacing the backend's `"(Branch: …)"` title. Only
   * invalidating the query is too late; the refetch has not landed when the
   * titler runs.
   */
  const handleBranchMessage = async (messageId: string) => {
    try {
      const forked = await chat.branchFromMessage(messageId);
      if (!forked) return;
      queryClient.setQueryData<Conversation[]>(conversationKeys.all, (prev) => {
        if (prev?.some((c) => c.id === forked.id)) return prev;
        return [forked, ...(prev ?? [])];
      });
      setActiveConversationId(forked.id);
      void queryClient.invalidateQueries({ queryKey: conversationKeys.all });
    } catch {
      // `branchFromMessage` already recorded the error on the hook, which the
      // composer surfaces; swallowing here avoids an unhandled rejection.
    }
  };

  const handleDeleteMessage = async (messageId: string) => {
    if (!window.confirm("Delete this message? The turn is removed from the conversation permanently.")) {
      return;
    }
    try {
      await chat.removeMessage(messageId);
      // Deleting a turn rewrites the persisted transcript, so any cached
      // history for this conversation is stale; the hook has already re-read it
      // into the live list, and this keeps the sidebar's preview/last-message
      // in step too.
      void queryClient.invalidateQueries({ queryKey: conversationKeys.all });
    } catch {
      // Error already surfaced by the hook.
    }
  };

  const handleEditMessage = async (messageId: string, content: string) => {
    try {
      await chat.editAndResend(messageId, content);
      void queryClient.invalidateQueries({ queryKey: conversationKeys.all });
    } catch {
      // Error already surfaced by the hook.
    }
  };

  const handleRegenerateMessage = async (messageId: string) => {
    try {
      await chat.regenerateMessage(messageId);
      void queryClient.invalidateQueries({ queryKey: conversationKeys.all });
    } catch {
      // Error already surfaced by the hook.
    }
  };

  const handleDraftPlan = async (convId: string, content: string) => {
    setPlanBusy(true);
    setPendingPlanTask(content.trim());
    try {
      const plan = await createPlan(convId, content.trim());
      setPendingPlan(mapPlan(plan));
      // Refresh the history so the new draft is listed. Refetching rather than
      // prepending matters: the pending review card renders the same title, and
      // a hand-prepended cache row would put that title in the history list too,
      // so the same plan would be on screen twice.
      void queryClient.invalidateQueries({
        queryKey: planKeys.byConversation(convId),
      });
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
      // Re-read so the history row reflects the decision rather than staying
      // "pending" until the conversation is reloaded. There is no plan delete in
      // the UI, so a refetch cannot resurrect a row that was removed here.
      if (convId) {
        void queryClient.invalidateQueries({
          queryKey: planKeys.byConversation(convId),
        });
      }
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
      if (activeConversationId) {
        void queryClient.invalidateQueries({
          queryKey: planKeys.byConversation(activeConversationId),
        });
      }
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
      if (isPersisted) {
        // The row already exists in the saved list; update it there (and in the
        // session list, in case it was opened from the canvas).
        queryClient.setQueryData<CachedArtifact[]>(
          artifactKeys.byConversation(activeConversationId),
          (prev) =>
            (prev ?? []).map((a) =>
              a.id === artifact.id
                ? {
                    ...a,
                    title: saved.title,
                    language: saved.language,
                    content: saved.content,
                    version: saved.version,
                    isActiveVersion: true,
                  }
                : a,
            ),
        );
        setSessionArtifacts((prev) =>
          prev.map((a) => (a.id === artifact.id ? persisted : a)),
        );
      } else {
        // A transient canvas item just became a real row: drop the transient
        // (its client id differs from the server id) and keep the persisted one.
        setSessionArtifacts((prev) => [
          ...prev.filter((a) => a.id !== artifact.id),
          persisted,
        ]);
      }
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
    if (!window.confirm("Delete this artifact? This cannot be undone.")) return;
    try {
      await deleteArtifact(artifact.id);
      queryClient.setQueryData<CachedArtifact[]>(
        artifactKeys.byConversation(activeConversationId),
        (prev) => (prev ?? []).filter((a) => a.id !== artifact.id),
      );
      setSessionArtifacts((prev) => prev.filter((a) => a.id !== artifact.id));
      setActiveArtifact((prev) => (prev && prev.id === artifact.id ? null : prev));
    } catch (err) {
      console.warn("Artifact delete failed:", err);
    }
  };

  const handleSendMessage = async (
    content: string,
    options: SendMessageOptions
  ) => {
    // Image generation is a separate path from a chat turn: it is not a graph
    // run, spends no tokens, and has no SSE stream. Dispatching it before the
    // plan/attachment logic keeps it from creating a conversation or uploading
    // files it has no use for.
    if (options.generateImage) {
      void chat.generateImage(content);
      return;
    }

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
          queryClient.setQueryData<Conversation[]>(conversationKeys.all, (prev) => {
            if (prev?.some((c) => c.id === convId)) return prev;
            return [conv, ...(prev ?? [])];
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

    const mode = resolveConversationMode({
      enableCode: options.enableCode,
      enableWeb: options.enableWeb,
      agentMode: options.agentMode,
    });

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
      {/* Mobile backdrop: only exists under `md`, and closes the drawer. */}
      {sidebarOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={closeSidebar}
          className="fixed inset-0 z-30 bg-black/50 md:hidden"
        />
      )}

      {/* Sidebar Navigation. Fixed off-canvas below `md`, static column above it. */}
      <div
        // `w-72` is load-bearing, not decorative: `-translate-x-full` resolves
        // against this element's own width, and a fixed box with only `left-0`
        // shrinks to zero — which makes 100% a translation of nothing and leaves
        // the drawer permanently on screen. The width matches the aside inside.
        className={`fixed inset-y-0 left-0 z-40 w-72 transition-transform duration-200 ease-out md:static md:z-auto md:w-auto md:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
      <Sidebar
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelectConversation={(id) => {
          handleSelectConversation(id);
          // Choosing something is the signal that the drawer has done its job.
          closeSidebar();
        }}
        onNewChat={() => {
          handleNewChat();
          closeSidebar();
        }}
        onDeleteConversation={handleDeleteConversation}
        onForkConversation={handleForkConversation}
        onShareConversation={handleShareConversation}
        onRenameConversation={handleRenameConversation}
        onPinConversation={handlePinConversation}
        activeTab={effectiveTab}
        setActiveTab={(tab) => {
          setActiveTab(tab);
          closeSidebar();
        }}
        currentModel={currentModel}
        onChangeModel={chooseModel}
        onSignOut={handleSignOut}
        showAdmin={isAdmin}
        onOpenCommandPalette={() => setPaletteOpen(true)}
      />
      </div>

      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        conversations={conversations}
        onSelectConversation={handleSelectConversation}
        onNewChat={handleNewChat}
        setActiveTab={setActiveTab}
        onChangeModel={chooseModel}
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
        {/* Desktop header: theme toggle + app title, hidden below md. */}
        <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-4 py-2.5 bg-[var(--bg-surface)] md:flex hidden">
          <div className="flex items-center gap-2">
            <div className="h-6 w-6 rounded bg-[var(--accent)]" />
            <span className="text-sm font-semibold text-[var(--text-primary)]">Nexus AI</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              aria-label="Toggle theme"
              onClick={() => setMode(mode === "light" ? "dark" : "light")}
              className="rounded-md p-2 text-[var(--text-secondary)] hover:bg-[var(--bg-surface)] transition-colors"
              title={mode === "light" ? "Switch to dark mode" : "Switch to light mode"}
            >
              {mode === "light" ? (
                <Moon className="h-5 w-5" />
              ) : (
                <Sun className="h-5 w-5" />
              )}
              <span className="hidden sm:inline text-[11px] text-[var(--text-muted)]">{
                mode === "light" ? "Dark" : "Light"
              }</span>
            </button>
            <button
              type="button"
              aria-label="Compare models side-by-side"
              onClick={() => setComparisonOpen(true)}
              className="rounded-md p-2 text-[var(--text-secondary)] hover:bg-[var(--bg-surface)] transition-colors hidden sm:flex"
              title="Compare two models side-by-side"
            >
              <Sliders className="w-5 h-5" />
              <span className="ml-1 text-[11px] text-[var(--text-muted)]">Compare</span>
            </button>
          </div>
        </div>
        {/* Mobile-only bar: without it the drawer has no handle on small screens. */}
        <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-3 py-2 md:hidden">
          <button
            type="button"
            aria-label="Open navigation"
            onClick={() => setSidebarOpen(true)}
            className="rounded-md p-2 text-[var(--text-secondary)] hover:bg-[var(--bg-surface)]"
          >
            <Menu className="h-5 w-5" />
          </button>
          {/* Theme toggle on mobile bar */}
          <button
            type="button"
            aria-label="Toggle theme"
            onClick={() => setMode(mode === "light" ? "dark" : "light")}
            className="ml-auto rounded-md p-2 text-[var(--text-secondary)] hover:bg-[var(--bg-surface)] transition-colors"
            title={mode === "light" ? "Switch to dark mode" : "Switch to light mode"}
          >
            {mode === "light" ? (
              <Moon className="h-5 w-5" />
            ) : (
              <Sun className="h-5 w-5" />
            )}
          </button>
          {/* The brand lives in the drawer; repeating it here gave the page two
              identical "Nexus AI" nodes inside <main>, which broke exact-text
              assertions (e2e/auth.spec.ts) and duplicated the app name on screen. */}
        </div>
        {effectiveTab === "chat" && (
          <div className="flex-1 flex h-full overflow-hidden relative">
            {/* Chat Column */}
            <div
              className={`flex flex-col h-full overflow-hidden transition-all duration-200 ${
                activeArtifact ? "w-full md:w-[52%]" : "w-full"
              }`}
            >
              {chat.isHydrating ? (
                <div className="flex-1 flex items-center justify-center text-sm text-[var(--text-muted)]">
                  Loading conversation…
                </div>
              ) : (
                <ChatArea
                  messages={messages}
                  isLoading={chat.isLoading}
                  statusLabel={chat.statusLabel}
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
                  conversationTitle={
                    conversations.find((c) => c.id === activeConversationId)
                      ?.title ?? null
                  }
                  onFeedback={handleFeedback}
                  onOpenArtifact={handleOpenArtifact}
                  onSelectCitation={setActiveCitation}
                  onDeleteMessage={handleDeleteMessage}
                  onEditMessage={handleEditMessage}
                  onRegenerate={handleRegenerateMessage}
                  onBranch={handleBranchMessage}
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
              {/* Mobile bottom composer: fixed at bottom on small screens so
                  the input is always reachable with thumbs. On desktop it stays
                  inline in the page flow. */}
              <div className="md:static fixed bottom-0 left-0 right-0 z-50 bg-[var(--bg-surface)] border-t border-[var(--border-subtle)] p-2 shadow-lg md:shadow-none md:border-t-0 md:p-0">
                <ChatInput
                  key={composerEpoch}
                  onSendMessage={handleSendMessage}
                  isLoading={chat.isLoading}
                  onStop={handleStop}
                  queuedMessages={chat.queuedMessages}
                  onCancelQueued={chat.cancelQueued}
                />
              </div>
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

            {/* Keyboard shortcuts guide (toggle with ?) */}
            <KeyboardShortcutsGuide open={showShortcuts} onClose={() => setShowShortcuts(false)} />

            {/* Model comparison modal */}
            {comparisonOpen && (
              <ModelComparison
                onClose={() => setComparisonOpen(false)}
                question={comparisonQuestion}
                modelA="gpt-4o-mini"
                modelB="gpt-4o"
              />
            )}
          </div>
        )}

        {effectiveTab === "files" && (
  <KnowledgeView />
)}
        {effectiveTab === "projects" && (
          <ProjectsView onOpenConversation={handleSelectConversation} />
        )}
        {effectiveTab === "usage" && <UsageView />}
        {effectiveTab === "settings" && <SettingsView />}
        {effectiveTab === "admin" && <AdminView />}
      </main>
    </div>
  );
}
