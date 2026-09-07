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
      <div className="glass-panel p-2.5 transition-all focus-within:border-violet-500/50 shadow-2xl shadow-black/40">
        {/* Attachment Chips */}
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 p-2 border-b border-[var(--border-subtle)] mb-2">
            {attachments.map((file, idx) => (
              <div
                key={idx}
                className="flex items-center gap-1.5 bg-[#0e121d] border border-[var(--border-subtle)] px-2.5 py-1 rounded-lg text-xs text-neutral-300"
              >
                <FileText className="w-3.5 h-3.5 text-cyan-400" />
                <span className="truncate max-w-[150px]">{file.name}</span>
                <button
                  onClick={() => removeAttachment(idx)}
                  className="hover:text-rose-400 ml-1 transition-colors"
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
          className="w-full bg-transparent resize-none text-neutral-100 placeholder-neutral-500 text-sm px-2 py-1.5 outline-none focus:ring-0 leading-relaxed max-h-44 overflow-y-auto"
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
              className="p-1.5 rounded-lg text-neutral-400 hover:text-white hover:bg-[var(--bg-surface)] transition-colors"
              title="Attach document or code"
            >
              <Paperclip className="w-4 h-4" />
            </button>

            {/* Web Search Toggle */}
            <button
              onClick={() => setEnableWeb(!enableWeb)}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                enableWeb
                  ? "bg-cyan-500/20 text-cyan-300 border border-cyan-500/40"
                  : "text-neutral-400 hover:text-white hover:bg-[var(--bg-surface)] border border-transparent"
              }`}
              title="Firecrawl Live Web Search"
            >
              <Globe className="w-3.5 h-3.5" />
              <span>Web</span>
            </button>

            {/* Python Sandbox Toggle */}
            <button
              onClick={() => setEnableCode(!enableCode)}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                enableCode
                  ? "bg-violet-500/20 text-violet-300 border border-violet-500/40"
                  : "text-neutral-400 hover:text-white hover:bg-[var(--bg-surface)] border border-transparent"
              }`}
              title="E2B Code Interpreter microVM"
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
                className="p-2 rounded-xl bg-rose-600 hover:bg-rose-500 text-white transition-all shadow-md shadow-rose-600/25"
                title="Stop generation"
              >
                <StopCircle className="w-4 h-4" />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!content.trim() && attachments.length === 0}
                className="p-2 rounded-xl bg-gradient-to-r from-violet-600 to-indigo-600 hover:from-violet-500 hover:to-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed text-white transition-all shadow-md shadow-violet-600/25 active:scale-95"
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
