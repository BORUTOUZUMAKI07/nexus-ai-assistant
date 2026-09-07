"use client";

import React, { useState, useRef } from "react";
import {
  Send,
  Paperclip,
  Globe,
  Code,
  X,
  FileText,
  StopCircle,
} from "lucide-react";

interface ChatInputProps {
  onSendMessage: (content: string, options: { enableWeb: boolean; enableCode: boolean; attachments: File[] }) => void;
  isLoading: boolean;
  onStop?: () => void;
}

export const ChatInput: React.FC<ChatInputProps> = ({ onSendMessage, isLoading, onStop }) => {
  const [content, setContent] = useState("");
  const [enableWeb, setEnableWeb] = useState(false);
  const [enableCode, setEnableCode] = useState(false);
  const [attachments, setAttachments] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleSend = () => {
    if ((!content.trim() && attachments.length === 0) || isLoading) return;
    onSendMessage(content, { enableWeb, enableCode, attachments });
    setContent("");
    setAttachments([]);
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

  const removeAttachment = (index: number) => {
    setAttachments((prev) => prev.filter((_, idx) => idx !== index));
  };

  const adjustTextareaHeight = () => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`;
    }
  };

  return (
    <div className="max-w-3xl mx-auto w-full px-4 pb-6">
      <div className="glass-panel p-2.5 transition-all focus-within:border-[var(--accent)] shadow-float">
        {/* Attachment Chips */}
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 p-2 border-b border-[var(--border-subtle)] mb-2">
            {attachments.map((file, idx) => (
              <div
                key={idx}
                className="flex items-center gap-1.5 bg-[var(--bg-main)] border border-[var(--border-subtle)] px-2.5 py-1 rounded-lg text-xs text-[var(--text-secondary)]"
              >
                <FileText className="w-3.5 h-3.5 text-[var(--accent)]" />
                <span className="truncate max-w-[150px]">{file.name}</span>
                <button
                  onClick={() => removeAttachment(idx)}
                  className="hover:text-[var(--status-danger)] ml-1 transition-colors"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Text Input Area */}
        <textarea
          ref={textareaRef}
          value={content}
          onChange={(e) => {
            setContent(e.target.value);
            adjustTextareaHeight();
          }}
          onKeyDown={handleKeyDown}
          placeholder="Ask Nexus anything, write code, search live web..."
          rows={1}
          className="w-full bg-transparent resize-none text-[var(--text-primary)] placeholder-[var(--text-faint)] text-sm px-2 py-1.5 outline-none focus:ring-0 leading-relaxed max-h-44 overflow-y-auto"
        />

        {/* Bottom Toolbar & Controls */}
        <div className="flex items-center justify-between pt-2 px-1 border-t border-[var(--border-subtle)] mt-1">
          {/* Action Toggles */}
          <div className="flex items-center gap-1.5">
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
                disabled={!content.trim() && attachments.length === 0}
                className="p-2 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-40 disabled:cursor-not-allowed text-white transition-all active:scale-95"
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
