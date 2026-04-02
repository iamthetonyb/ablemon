'use client';

import { useChat } from '@ai-sdk/react';
import { DefaultChatTransport } from 'ai';
import { useState, useRef, useEffect, useMemo } from 'react';
import { Send } from 'lucide-react';

/**
 * Chat panel — only mounted when user clicks the bubble.
 * useChat + DefaultChatTransport initialize here, not on page load.
 * API calls only fire when user sends a message.
 */
export default function ChatPanel() {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const transport = useMemo(() => new DefaultChatTransport({ api: '/api/chat' }), []);
  const { messages, sendMessage, status, error } = useChat({ transport });
  const isLoading = status === 'streaming' || status === 'submitted';

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || isLoading) return;
    setInput('');
    await sendMessage({ text });
  }

  return (
    <div className="fixed bottom-24 right-6 z-50 w-[400px] h-[540px] flex flex-col
      rounded-2xl overflow-hidden border border-border-glass
      bg-surface-elevated/95 backdrop-blur-2xl shadow-2xl shadow-black/50">

      {/* Header */}
      <div className="px-5 py-4 border-b border-border-subtle flex items-center gap-3">
        <span className="status-dot status-dot-active" />
        <div>
          <h3 className="text-[14px] font-semibold text-text-primary">ABLE</h3>
          <p className="text-[11px] text-text-muted">AGI Mission Control</p>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-text-muted text-[13px] mt-16">
            <p className="text-2xl mb-3">&#9876;</p>
            <p>ABLE is online.</p>
            <p className="text-[12px] mt-1">Ask anything — strategy, code, deployments.</p>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[85%] px-4 py-2.5 rounded-2xl text-[13px] leading-relaxed whitespace-pre-wrap
                ${msg.role === 'user'
                  ? 'bg-gold-400/15 text-gold-100 rounded-br-md'
                  : 'bg-white/5 text-text-secondary rounded-bl-md border border-border-subtle'
                }`}
            >
              {msg.parts.map((part, i) => {
                if (part.type === 'text') return <span key={i}>{part.text}</span>;
                return null;
              })}
            </div>
          </div>
        ))}

        {isLoading && messages[messages.length - 1]?.role === 'user' && (
          <div className="flex justify-start">
            <div className="bg-white/5 border border-border-subtle px-4 py-3 rounded-2xl rounded-bl-md">
              <div className="flex gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-gold-400 animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-1.5 h-1.5 rounded-full bg-gold-400 animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-1.5 h-1.5 rounded-full bg-gold-400 animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
            </div>
          </div>
        )}

        {error && (
          <div className="text-xs text-error bg-error/10 px-3 py-2 rounded-lg">
            Connection error — check API key
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="px-4 py-3 border-t border-border-subtle">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Message ABLE..."
            className="input-glass flex-1 !min-h-[40px] !py-2 !text-[13px]"
          />
          <button
            type="submit"
            disabled={isLoading || !input.trim()}
            className="btn-gold !min-h-[40px] !px-4 !text-[13px]"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </form>
    </div>
  );
}
