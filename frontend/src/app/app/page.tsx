"use client";

import React, { useState, useEffect, useCallback } from "react";
import { Sidebar, ConversationItem } from "@/components/Sidebar";
import {
  ChatArea,
  MessageItem,
  CitationItem,
  ToolCallItem,
} from "@/components/ChatArea";
import { ChatInput } from "@/components/ChatInput";
import { KnowledgeView } from "@/components/KnowledgeView";
import { UsageView } from "@/components/UsageView";
import { SettingsView } from "@/components/SettingsView";
import { AdminView } from "@/components/AdminView";
import { AuthModal } from "@/components/AuthModal";
import {
  fetchConversations,
  fetchConversation,
  createConversation,
  deleteConversation,
  uploadFile,
  ConversationMessage,
} from "@/lib/api";
import { getAccessToken, clearAccessToken } from "@/lib/auth";
import { useNexusChat, NexusMessage } from "@/hooks/useNexusChat";

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

export default function Home() {
  const [activeTab, setActiveTab] = useState<"chat" | "files" | "settings" | "usage" | "admin">("chat");
  const [currentModel, setCurrentModel] = useState("llama-3.3-70b-versatile");
  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [activeConversationId, setActiveConversationId] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [isAuthOpen, setIsAuthOpen] = useState(false);

  const chat = useNexusChat({
    conversationId: activeConversationId,
    model: currentModel,
  });

  // Open the login gate when no session token exists (client-side check only,
  // so the server render and hydration always agree on a closed modal).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!getAccessToken()) setIsAuthOpen(true);
  }, []);

  const handleNewChat = useCallback(async () => {
    try {
      const created = await createConversation("New Conversation", "normal", currentModel);
      const newConv: ConversationItem = {
        id: created.id,
        title: created.title || "New Conversation",
        model: currentModel,
        is_pinned: created.is_pinned,
        updated_at: new Date(created.updated_at).toLocaleDateString(),
      };
      setConversations((prev) => [newConv, ...prev]);
      setActiveConversationId(created.id);
      chat.clearMessages();
    } catch {
      // Without a reachable backend we cannot create durable conversations;
      // keep the sidebar empty rather than inventing local state.
      chat.clearMessages();
    }
    setActiveTab("chat");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentModel]);

  const loadHistory = useCallback(
    async (conversationId: string) => {
      if (!conversationId) return;
      setHistoryLoading(true);
      try {
        const detail = await fetchConversation(conversationId);
        chat.setMessages((detail.messages ?? []).map(mapServerMessage));
      } catch (err) {
        console.warn("Could not load conversation history:", err);
        chat.setMessages([]);
      } finally {
        setHistoryLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  // Load conversations from backend once the user is authenticated
  useEffect(() => {
    if (isAuthOpen) return;
    if (!getAccessToken()) return;
    fetchConversations()
      .then((page) => {
        if (page?.items && page.items.length > 0) {
          const mapped: ConversationItem[] = page.items.map((item) => ({
            id: item.id,
            title: item.title,
            model: item.model,
            is_pinned: item.is_pinned,
            updated_at: new Date(item.updated_at).toLocaleDateString(),
          }));
          setConversations(mapped);
          setActiveConversationId(mapped[0].id);
          void loadHistory(mapped[0].id);
        } else {
          // Auto-create a first conversation so the chat surface is usable
          handleNewChat();
        }
      })
      .catch((err) => {
        console.warn("Backend conversation list unavailable:", err);
      });
  }, [isAuthOpen, loadHistory, handleNewChat]);

  const handleSignOut = () => {
    clearAccessToken();
    setConversations([]);
    setActiveConversationId("");
    chat.clearMessages();
    setIsAuthOpen(true);
  };

  const handleSelectConversation = (id: string) => {
    setActiveConversationId(id);
    setActiveTab("chat");
    void loadHistory(id);
  };

  const handleDeleteConversation = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteConversation(id);
    } catch {
      // Ignored — the list will refresh from the backend on next load.
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

  const handleStop = () => {
    chat.stop();
  };

  const handleSendMessage = async (
    content: string,
    options: { enableWeb: boolean; enableCode: boolean; attachments: File[] }
  ) => {
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

    const mode = options.enableCode ? "code" : options.enableWeb ? "research" : "normal";
    void chat.sendMessage(content, { mode });
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
      created_at: m.createdAt,
    };
  });

  const handleFeedback = (messageId: string, feedback: "thumbs_up" | "thumbs_down") => {
    console.log("Feedback recorded:", messageId, feedback);
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[var(--bg-main)] text-white">
      {/* Sidebar Navigation */}
      <Sidebar
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelectConversation={handleSelectConversation}
        onNewChat={handleNewChat}
        onDeleteConversation={handleDeleteConversation}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        currentModel={currentModel}
        onChangeModel={setCurrentModel}
        onSignOut={handleSignOut}
      />

      <AuthModal
        isOpen={isAuthOpen}
        onClose={() => {
          clearAccessToken();
          setIsAuthOpen(false);
        }}
        onSuccess={() => setIsAuthOpen(false)}
      />

      {/* Main Content Area */}
      <main className="flex-1 flex flex-col h-full overflow-hidden relative">
        {activeTab === "chat" && (
          <>
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
                      }
                    : null
                }
                onResolveHITL={chat.resolveHITL}
                onFeedback={handleFeedback}
              />
            )}
            <ChatInput
              onSendMessage={handleSendMessage}
              isLoading={chat.isLoading}
              onStop={handleStop}
            />
          </>
        )}

        {activeTab === "files" && <KnowledgeView />}
        {activeTab === "usage" && <UsageView />}
        {activeTab === "settings" && <SettingsView />}
        {activeTab === "admin" && <AdminView />}
      </main>
    </div>
  );
}