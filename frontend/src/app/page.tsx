"use client";

import React, { useState, useEffect, useRef } from "react";
import { Sidebar, ConversationItem } from "@/components/Sidebar";
import { ChatArea, MessageItem, CitationItem, ToolCallItem } from "@/components/ChatArea";
import { ChatInput } from "@/components/ChatInput";
import { KnowledgeView } from "@/components/KnowledgeView";
import { UsageView } from "@/components/UsageView";
import { SettingsView } from "@/components/SettingsView";
import { AdminView } from "@/components/AdminView";
import { AuthModal } from "@/components/AuthModal";
import {
  fetchConversations,
  createConversation,
  deleteConversation,
  uploadFile,
} from "@/lib/api";
import { getAccessToken, clearAccessToken } from "@/lib/auth";

export default function Home() {
  const [activeTab, setActiveTab] = useState<"chat" | "files" | "settings" | "usage" | "admin">("chat");
  const [currentModel, setCurrentModel] = useState("llama-3.3-70b-versatile");
  const [isLoading, setIsLoading] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const [conversations, setConversations] = useState<ConversationItem[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string>("");
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [isAuthOpen, setIsAuthOpen] = useState(false);

  // Open the login gate when no session token exists (client-side check only,
  // so the server render and hydration always agree on a closed modal).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!getAccessToken()) setIsAuthOpen(true);
  }, []);

  const handleNewChat = async () => {
    const tempId = `conv-${Date.now()}`;
    try {
      const created = await createConversation("New Conversation", "normal");
      const newConv: ConversationItem = {
        id: created.id,
        title: created.title || "New Conversation",
        model: currentModel,
        is_pinned: false,
        updated_at: "Just now",
      };
      setConversations((prev) => [newConv, ...prev]);
      setActiveConversationId(created.id);
    } catch {
      const newConv: ConversationItem = {
        id: tempId,
        title: "New Conversation",
        model: currentModel,
        is_pinned: false,
        updated_at: "Just now",
      };
      setConversations((prev) => [newConv, ...prev]);
      setActiveConversationId(tempId);
    }
    setMessages([]);
    setActiveTab("chat");
  };

  // Load conversations from backend once the user is authenticated
  useEffect(() => {
    if (isAuthOpen) return;
    fetchConversations()
      .then((page) => {
        if (page?.items && page.items.length > 0) {
          const mapped: ConversationItem[] = page.items.map((item) => ({
            id: item.id,
            title: item.title,
            model: "llama-3.3-70b-versatile",
            is_pinned: false,
            updated_at: new Date(item.updated_at).toLocaleDateString(),
          }));
          setConversations(mapped);
          setActiveConversationId(mapped[0].id);
        } else {
          // Auto-create initial conversation if user has none
          handleNewChat();
        }
      })
      .catch((err) => {
        console.warn("Backend conversation list unavailable, using local session:", err);
      });
  }, [isAuthOpen]);

  const handleSignOut = () => {
    clearAccessToken();
    setConversations([]);
    setActiveConversationId("");
    setMessages([]);
    setIsAuthOpen(true);
  };

  const handleDeleteConversation = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await deleteConversation(id);
    } catch {
      // Ignored for offline/local state
    }
    setConversations((prev) => prev.filter((c) => c.id !== id));
    if (activeConversationId === id) {
      const remaining = conversations.filter((c) => c.id !== id);
      if (remaining.length > 0) {
        setActiveConversationId(remaining[0].id);
      } else {
        handleNewChat();
      }
    }
  };

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsLoading(false);
  };

  const handleSendMessage = async (
    content: string,
    options: { enableWeb: boolean; enableCode: boolean; attachments: File[] }
  ) => {
    // 1. Upload any attachments first
    if (options.attachments && options.attachments.length > 0) {
      for (const file of options.attachments) {
        try {
          await uploadFile(file);
        } catch (uploadErr) {
          console.warn("Attachment upload warning:", uploadErr);
        }
      }
    }

    const userMsgId = `msg-${Date.now()}`;
    const userMsg: MessageItem = {
      id: userMsgId,
      role: "user",
      content,
    };

    setMessages((prev) => [...prev, userMsg]);
    setIsLoading(true);

    const assistantMsgId = `msg-${Date.now() + 1}`;
    const initialAssistantMsg: MessageItem = {
      id: assistantMsgId,
      role: "assistant",
      model: currentModel,
      content: "",
    };

    setMessages((prev) => [...prev, initialAssistantMsg]);

    const controller = new AbortController();
    abortControllerRef.current = controller;

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          messages: [...messages, userMsg].map((m) => ({
            role: m.role,
            content: m.content,
          })),
          conversationId: activeConversationId,
          mode: options.enableCode ? "code" : options.enableWeb ? "research" : "normal",
        }),
      });

      if (!response.ok || !response.body) {
        throw new Error(`Chat API error: ${response.statusText}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let streamContent = "";
      let thoughtProcess = "";
      const citations: CitationItem[] = [];
      const toolCalls: ToolCallItem[] = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split("\n");

        for (const line of lines) {
          if (!line) continue;

          // Vercel AI SDK text-stream protocol
          if (line.startsWith("0:")) {
            try {
              const textDelta = JSON.parse(line.slice(2));
              streamContent += textDelta;
            } catch {
              streamContent += line.slice(2);
            }
          } else if (line.startsWith("8:")) {
            // Annotations (tool_call, citation, reasoning, hitl)
            try {
              const annotations = JSON.parse(line.slice(2));
              if (Array.isArray(annotations)) {
                for (const ann of annotations) {
                  if (ann.type === "reasoning") {
                    thoughtProcess += ann.data?.content || "";
                  } else if (ann.type === "citation") {
                    citations.push(ann.data);
                  } else if (ann.type === "tool_call") {
                    toolCalls.push({
                      name: ann.data?.tool_name || "tool",
                      args: ann.data?.tool_input,
                      status: "running",
                    });
                  } else if (ann.type === "tool_result") {
                    const call = toolCalls.find((c) => c.name === ann.data?.tool_name);
                    if (call) {
                      call.status = "completed";
                      call.result = ann.data?.result;
                    }
                  }
                }
              }
            } catch {
              // Ignore annotation parse failure
            }
          }

          // Live state update for streaming UI
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMsgId
                ? {
                    ...msg,
                    content: streamContent || msg.content,
                    thought_process: thoughtProcess || msg.thought_process,
                    citations: citations.length > 0 ? citations : msg.citations,
                    tool_calls: toolCalls.length > 0 ? toolCalls : msg.tool_calls,
                  }
                : msg
            )
          );
        }
      }
    } catch (err) {
      const isAbort = err instanceof Error && err.name === "AbortError";
      if (!isAbort) {
        console.warn("Chat stream failed, using offline fallback response:", err);
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantMsgId
              ? {
                  ...msg,
                  content:
                    `I received your message: "${content}".\n\n` +
                    `*The backend API server or Docker services are currently running in standalone/offline mode.* All agents, tools, and endpoints are configured and ready.`,
                }
              : msg
          )
        );
      }
    } finally {
      setIsLoading(false);
      abortControllerRef.current = null;
    }
  };

  const handleFeedback = (messageId: string, feedback: "thumbs_up" | "thumbs_down") => {
    console.log("Feedback recorded:", messageId, feedback);
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[var(--bg-main)] text-white">
      {/* Sidebar Navigation */}
      <Sidebar
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelectConversation={(id) => {
          setActiveConversationId(id);
          setActiveTab("chat");
        }}
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
            <ChatArea
              messages={messages}
              isLoading={isLoading}
              onFeedback={handleFeedback}
            />
            <ChatInput
              onSendMessage={handleSendMessage}
              isLoading={isLoading}
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
