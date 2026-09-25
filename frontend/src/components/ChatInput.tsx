"use client";

import React, { useState, useRef, useCallback, useEffect } from "react";
import {
  Send,
  Paperclip,
  Globe,
  Code,
  X,
  FileText,
  StopCircle,
  Sparkles,
  Zap,
  BookOpen,
  Mic,
  MicOff,
  Image as ImageIcon,
} from "lucide-react";

export interface SendMessageOptions {
  enableWeb: boolean;
  enableCode: boolean;
  attachments: File[];
  agentMode?: "fast" | "deep";
  imageDataUrl?: string; // base64 data URL for inline image
}

interface ChatInputProps {
  onSendMessage: (content: string, options: SendMessageOptions) => void;
  isLoading: boolean;
  onStop?: () => void;
}

const PROMPT_TEMPLATES = [
  {
    title: "Senior Code Review",
    desc: "Evaluate correctness, security & performance",
    prompt:
      "Please perform a senior-level code review on the following code:\n\n```\n\n```\n\nEvaluate: 1. Correctness 2. Security vulnerabilities 3. Edge cases & performance.",
  },
  {
    title: "Deep Research Brief",
    desc: "Structured multi-perspective analysis",
    prompt:
      "Produce a comprehensive, structured research brief on: \n\nInclude: Executive Summary, Key Findings, Technical Impact, Challenges, and Actionable Recommendations.",
  },
  {
    title: "Root Cause Debugging",
    desc: "Diagnose stack trace & fix",
    prompt:
      "Diagnose this error traceback and provide the minimal verified fix:\n\n```\n\n```",
  },
  {
    title: "System Architecture",
    desc: "Design components & data flow",
    prompt:
      "Design an enterprise-grade system architecture for: \n\nDetail: Service boundaries, DB schemas, cache strategy, scalability bottlenecks, and failure modes.",
  },
];

const SLASH_COMMANDS = [
  { command: "/code", label: "Code Subagent", desc: "Activate Python sandbox & code agent", icon: "⚡" },
  { command: "/web", label: "Web Search", desc: "Enable live web search (CRAG)", icon: "🌐" },
  { command: "/deep", label: "Deep Agent", desc: "LangGraph Tree-of-Thought reasoning", icon: "🧠" },
  { command: "/doc", label: "Attach Document", desc: "Open document picker", icon: "📎" },
  { command: "/review", label: "Code Review", desc: "Senior-level code analysis", icon: "🔍" },
  { command: "/research", label: "Research Brief", desc: "Structured multi-perspective analysis", icon: "📋" },
];

export const ChatInput: React.FC<ChatInputProps> = ({
  onSendMessage,
  isLoading,
  onStop,
}) => {
  const [content, setContent] = useState("");
  const [enableWeb, setEnableWeb] = useState(false);
  const [enableCode, setEnableCode] = useState(false);
  const [agentMode, setAgentMode] = useState<"fast" | "deep">("deep");
  const [showPrompts, setShowPrompts] = useState(false);
  const [attachments, setAttachments] = useState<File[]>([]);

  // Image state
  const [imageDataUrl, setImageDataUrl] = useState<string | null>(null);
  const [imageName, setImageName] = useState<string>("");
  const imageInputRef = useRef<HTMLInputElement>(null);

  // Slash command state
  const [showSlash, setShowSlash] = useState(false);
  const [selectedSlashIdx, setSelectedSlashIdx] = useState(0);

  // Drag-and-drop state
  const [isDragging, setIsDragging] = useState(false);
  const dragCountRef = useRef(0);

  // Voice recording state
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Filtered slash commands based on what user typed after '/'
  const slashQuery = content.startsWith("/") ? content.slice(1).toLowerCase() : "";
  const filteredSlash = SLASH_COMMANDS.filter(
    (c) => c.command.slice(1).startsWith(slashQuery) || c.label.toLowerCase().startsWith(slashQuery)
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Slash command keyboard navigation
    if (showSlash && filteredSlash.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedSlashIdx((i) => (i + 1) % filteredSlash.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedSlashIdx((i) => (i - 1 + filteredSlash.length) % filteredSlash.length);
        return;
      }
      if (e.key === "Tab" || e.key === "Enter") {
        e.preventDefault();
        applySlashCommand(filteredSlash[selectedSlashIdx]);
        return;
      }
      if (e.key === "Escape") {
        setShowSlash(false);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const applySlashCommand = (cmd: typeof SLASH_COMMANDS[0]) => {
    if (cmd.command === "/code") { setEnableCode(true); setContent(""); }
    else if (cmd.command === "/web") { setEnableWeb(true); setContent(""); }
    else if (cmd.command === "/deep") { setAgentMode("deep"); setContent(""); }
    else if (cmd.command === "/doc") { fileInputRef.current?.click(); setContent(""); }
    else if (cmd.command === "/review") {
      setContent(PROMPT_TEMPLATES[0].prompt);
    } else if (cmd.command === "/research") {
      setContent(PROMPT_TEMPLATES[1].prompt);
    } else {
      setContent("");
    }
    setShowSlash(false);
    setSelectedSlashIdx(0);
    setTimeout(() => textareaRef.current?.focus(), 50);
  };

  const handleSend = () => {
    if ((!content.trim() && attachments.length === 0 && !imageDataUrl) || isLoading) return;
    const options: SendMessageOptions = {
      enableWeb,
      enableCode,
      attachments,
      imageDataUrl: imageDataUrl ?? undefined,
    };
    if (agentMode !== "deep") {
      options.agentMode = agentMode;
    }
    onSendMessage(content, options);
    setContent("");
    setAttachments([]);
    setImageDataUrl(null);
    setImageName("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const filesArr = Array.from(e.target.files);
      setAttachments((prev) => [...prev, ...filesArr]);
    }
  };

  // Drag-and-drop handlers
  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    dragCountRef.current++;
    setIsDragging(true);
  };
  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    dragCountRef.current--;
    if (dragCountRef.current === 0) setIsDragging(false);
  };
  const handleDragOver = (e: React.DragEvent) => { e.preventDefault(); };
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    dragCountRef.current = 0;
    setIsDragging(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length) {
      const imageFiles = files.filter((f) => f.type.startsWith("image/"));
      const otherFiles = files.filter((f) => !f.type.startsWith("image/"));
      if (imageFiles.length > 0) {
        const reader = new FileReader();
        reader.onload = () => { setImageDataUrl(reader.result as string); setImageName(imageFiles[0].name); };
        reader.readAsDataURL(imageFiles[0]);
      }
      if (otherFiles.length > 0) {
        setAttachments((prev) => [...prev, ...otherFiles]);
      }
    }
  };

  const handleImageChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setImageName(file.name);
    const reader = new FileReader();
    reader.onload = () => {
      setImageDataUrl(reader.result as string);
    };
    reader.readAsDataURL(file);
    // reset input so same file can be re-selected
    if (imageInputRef.current) imageInputRef.current.value = "";
  };

  const removeAttachment = (index: number) => {
    setAttachments((prev) => prev.filter((_, idx) => idx !== index));
  };

  const adjustTextareaHeight = () => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(
        textareaRef.current.scrollHeight,
        180
      )}px`;
    }
  };

  // ── Voice recording ──────────────────────────────────────────────────────

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "audio/webm";
      const recorder = new MediaRecorder(stream, { mimeType });
      audioChunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(audioChunksRef.current, { type: mimeType });
        await transcribeBlob(blob);
      };
      recorder.start(250); // collect chunks every 250 ms
      mediaRecorderRef.current = recorder;
      setIsRecording(true);
    } catch {
      alert("Microphone access denied. Please allow microphone access in your browser.");
    }
  }, []);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
    setIsRecording(false);
  }, []);

  const transcribeBlob = async (blob: Blob) => {
    setIsTranscribing(true);
    try {
      const formData = new FormData();
      formData.append("file", blob, "recording.webm");
      const res = await fetch("/api/audio/transcribe", {
        method: "POST",
        body: formData,
      });
      if (!res.ok) throw new Error("Transcription failed");
      const data = (await res.json()) as { text: string };
      if (data.text) {
        setContent((prev) =>
          prev ? `${prev} ${data.text}` : data.text
        );
        // expand textarea
        setTimeout(() => {
          if (textareaRef.current) {
            textareaRef.current.style.height = "auto";
            textareaRef.current.style.height = `${Math.min(
              textareaRef.current.scrollHeight,
              180
            )}px`;
            textareaRef.current.focus();
          }
        }, 50);
      }
    } catch {
      alert("Transcription error – please try again.");
    } finally {
      setIsTranscribing(false);
    }
  };

  const handleMicClick = () => {
    if (isRecording) {
      stopRecording();
    } else {
      void startRecording();
    }
  };

  const canSend =
    content.trim().length > 0 || attachments.length > 0 || imageDataUrl !== null;

  return (
    <div
      className="relative p-4 md:px-12 w-full max-w-4xl mx-auto"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {/* Drag-and-drop overlay */}
      {isDragging && (
        <div className="absolute inset-0 z-50 m-1 rounded-2xl border-2 border-dashed border-[var(--accent)] bg-[var(--accent-soft)] flex items-center justify-center pointer-events-none">
          <div className="text-center">
            <span className="text-2xl">📂</span>
            <p className="text-sm font-semibold text-[var(--accent)] mt-1">Drop files or images here</p>
          </div>
        </div>
      )}
      <div className="relative border border-[var(--border-strong)] rounded-2xl bg-[var(--bg-surface-elevated)] p-2.5 shadow-2xl focus-within:border-[var(--accent)] transition-all">
        {/* Image Preview */}
        {imageDataUrl && (
          <div className="relative mb-2 inline-block">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={imageDataUrl}
              alt={imageName}
              className="max-h-32 max-w-xs rounded-lg border border-[var(--border-subtle)] object-cover"
            />
            <button
              onClick={() => { setImageDataUrl(null); setImageName(""); }}
              className="absolute -top-1.5 -right-1.5 p-0.5 rounded-full bg-[var(--bg-surface-elevated)] border border-[var(--border-subtle)] text-[var(--text-muted)] hover:text-white"
              title="Remove image"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        )}

        {/* Attachment Previews */}
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2 p-1.5 border-b border-[var(--border-subtle)]">
            {attachments.map((file, idx) => (
              <div
                key={idx}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-xs text-[var(--text-secondary)]"
              >
                <FileText className="w-3.5 h-3.5 text-[var(--accent)]" />
                <span className="truncate max-w-[140px]">{file.name}</span>
                <button
                  onClick={() => removeAttachment(idx)}
                  className="hover:text-white p-0.5"
                  title="Remove attachment"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Slash Command Popover */}
        {showSlash && filteredSlash.length > 0 && (
          <div className="absolute bottom-full left-0 mb-2 w-72 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)] p-1.5 shadow-2xl z-50 space-y-0.5">
            <div className="text-[10px] font-semibold text-[var(--text-muted)] px-2 py-1 uppercase tracking-wider">
              Commands
            </div>
            {filteredSlash.map((cmd, idx) => (
              <button
                key={cmd.command}
                type="button"
                onMouseDown={(e) => { e.preventDefault(); applySlashCommand(cmd); }}
                className={`w-full text-left px-2.5 py-2 rounded-lg flex items-center gap-2.5 transition-colors ${
                  idx === selectedSlashIdx
                    ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                    : "hover:bg-[var(--bg-surface)] text-[var(--text-secondary)]"
                }`}
              >
                <span className="text-lg leading-none">{cmd.icon}</span>
                <div>
                  <div className="text-xs font-semibold text-white">{cmd.command} <span className="text-[var(--text-muted)] font-normal">{cmd.label}</span></div>
                  <div className="text-[10px] text-[var(--text-faint)]">{cmd.desc}</div>
                </div>
              </button>
            ))}
          </div>
        )}

        {/* Input Textarea */}
        <textarea
          ref={textareaRef}
          value={content}
          onChange={(e) => {
            const val = e.target.value;
            setContent(val);
            // Show slash popover when message starts with /
            if (val.startsWith("/") && val.length <= 20) {
              setShowSlash(true);
              setSelectedSlashIdx(0);
            } else {
              setShowSlash(false);
            }
            adjustTextareaHeight();
          }}
          onKeyDown={handleKeyDown}
          placeholder={
            isTranscribing
              ? "Transcribing speech…"
              : isRecording
              ? "Recording… click 🎙️ again to stop"
              : "Ask Nexus anything, write code, search live web..."
          }
          rows={1}
          disabled={isTranscribing}
          className="w-full bg-transparent resize-none text-[var(--text-primary)] placeholder-[var(--text-faint)] text-sm px-2 py-1.5 outline-none focus:ring-0 leading-relaxed max-h-44 overflow-y-auto"
        />

        {/* Bottom Toolbar & Controls */}
        <div className="flex items-center justify-between pt-2 px-1 border-t border-[var(--border-subtle)] mt-1">
          {/* Action Toggles */}
          <div className="flex items-center gap-1.5 flex-wrap">
            {/* Document attach */}
            <input
              type="file"
              ref={fileInputRef}
              onChange={handleFileChange}
              multiple
              className="hidden"
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-white hover:bg-[var(--bg-surface)] transition-colors"
              title="Attach document or code"
            >
              <Paperclip className="w-4 h-4" />
            </button>

            {/* Image attach */}
            <input
              type="file"
              ref={imageInputRef}
              accept="image/*"
              onChange={handleImageChange}
              className="hidden"
            />
            <button
              onClick={() => imageInputRef.current?.click()}
              className={`p-1.5 rounded-lg transition-colors ${
                imageDataUrl
                  ? "text-[var(--accent)] bg-[var(--accent-soft)]"
                  : "text-[var(--text-muted)] hover:text-white hover:bg-[var(--bg-surface)]"
              }`}
              title="Attach image for vision analysis"
            >
              <ImageIcon className="w-4 h-4" />
            </button>

            {/* Microphone / STT */}
            <button
              onClick={handleMicClick}
              disabled={isTranscribing}
              className={`p-1.5 rounded-lg transition-all ${
                isRecording
                  ? "text-[var(--status-danger)] bg-[var(--status-danger)]/10 animate-pulse"
                  : isTranscribing
                  ? "text-[var(--text-faint)] cursor-not-allowed"
                  : "text-[var(--text-muted)] hover:text-white hover:bg-[var(--bg-surface)]"
              }`}
              title={
                isRecording
                  ? "Stop recording"
                  : isTranscribing
                  ? "Transcribing…"
                  : "Voice input (Groq Whisper)"
              }
            >
              {isRecording ? (
                <MicOff className="w-4 h-4" />
              ) : (
                <Mic className="w-4 h-4" />
              )}
            </button>

            {/* Agent Mode Toggle */}
            <button
              onClick={() =>
                setAgentMode(agentMode === "deep" ? "fast" : "deep")
              }
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                agentMode === "deep"
                  ? "bg-[var(--accent-soft)] text-[var(--accent)] border border-[var(--accent)]/40"
                  : "text-[var(--text-muted)] hover:text-white hover:bg-[var(--bg-surface)] border border-transparent"
              }`}
              title={
                agentMode === "deep"
                  ? "Deep Agent: LangGraph Tree-of-Thoughts & Multi-Agent Planning"
                  : "Fast Chat: Direct Conversational LLM Response"
              }
            >
              {agentMode === "deep" ? (
                <>
                  <Sparkles className="w-3.5 h-3.5 text-[var(--accent)]" />
                  <span>Deep Agent</span>
                </>
              ) : (
                <>
                  <Zap className="w-3.5 h-3.5 text-[var(--status-warning)]" />
                  <span>Fast Chat</span>
                </>
              )}
            </button>

            {/* Web Search Toggle */}
            <button
              onClick={() => setEnableWeb(!enableWeb)}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                enableWeb
                  ? "bg-[var(--accent-soft)] text-[var(--accent-hover)] border border-[var(--accent)]"
                  : "text-[var(--text-muted)] hover:text-white hover:bg-[var(--bg-surface)] border border-transparent"
              }`}
              title="Live web search"
            >
              <Globe className="w-3.5 h-3.5" />
              <span>Web</span>
            </button>

            {/* Python Sandbox Toggle */}
            <button
              onClick={() => setEnableCode(!enableCode)}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                enableCode
                  ? "bg-[var(--accent-soft)] text-[var(--accent-hover)] border border-[var(--accent)]"
                  : "text-[var(--text-muted)] hover:text-white hover:bg-[var(--bg-surface)] border border-transparent"
              }`}
              title="Code interpreter sandbox"
            >
              <Code className="w-3.5 h-3.5" />
              <span>Code</span>
            </button>

            {/* Prompt Library Templates */}
            <div className="relative">
              <button
                type="button"
                onClick={() => setShowPrompts(!showPrompts)}
                className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                  showPrompts
                    ? "bg-[var(--accent-soft)] text-[var(--accent-hover)] border border-[var(--accent)]"
                    : "text-[var(--text-muted)] hover:text-white hover:bg-[var(--bg-surface)] border border-transparent"
                }`}
                title="Select from prompt library"
              >
                <BookOpen className="w-3.5 h-3.5" />
                <span>Prompts</span>
              </button>

              {showPrompts && (
                <div className="absolute bottom-full left-0 mb-2 w-72 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface-elevated)] p-2 shadow-2xl z-50 space-y-1">
                  <div className="text-[11px] font-semibold text-[var(--text-muted)] px-2 py-1">
                    Prompt Library
                  </div>
                  {PROMPT_TEMPLATES.map((tpl) => (
                    <button
                      key={tpl.title}
                      type="button"
                      onClick={() => {
                        setContent(tpl.prompt);
                        setShowPrompts(false);
                        if (textareaRef.current) {
                          textareaRef.current.focus();
                        }
                      }}
                      className="w-full text-left p-2 rounded-lg hover:bg-[var(--bg-surface)] transition-colors group"
                    >
                      <div className="text-xs font-medium text-white group-hover:text-[var(--accent)]">
                        {tpl.title}
                      </div>
                      <div className="text-[10px] text-[var(--text-muted)] truncate">
                        {tpl.desc}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Send / Stop Button */}
          <div>
            {isLoading ? (
              <button
                onClick={onStop}
                className="p-2 rounded-lg bg-[var(--status-danger)] hover:opacity-90 text-white transition-all"
                title="Stop generation"
              >
                <StopCircle className="w-4 h-4" />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!canSend}
                className="p-2 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-40 disabled:cursor-not-allowed text-[var(--accent-foreground)] transition-all active:scale-95 shadow-[0_0_12px_var(--accent-glow)]"
                title="Send message (Enter)"
              >
                <Send className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
