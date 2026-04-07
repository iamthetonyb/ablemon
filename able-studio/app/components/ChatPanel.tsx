'use client';

import { useState, useRef, useEffect, KeyboardEvent } from 'react';
import { useChat } from '@ai-sdk/react';
import { TextStreamChatTransport } from 'ai';
import { Send, Loader } from 'lucide-react';

export default function ChatPanel() {
  const { messages, sendMessage, status, error } = useChat({
    transport: new TextStreamChatTransport({ api: '/api/chat' }),
  });
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const isLoading = status === 'streaming' || status === 'submitted';

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  function submit() {
    const text = input.trim();
    if (!text || isLoading) return;
    setInput('');
    sendMessage({ role: 'user', parts: [{ type: 'text', text }] });
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="fixed bottom-24 right-6 z-50 w-[400px] h-[540px] rounded-2xl border border-border-glass bg-surface-elevated/95 backdrop-blur-2xl shadow-2xl shadow-black/50 flex flex-col overflow-hidden animate-slide-in">
      {/* Header */}
      <div className="border-b border-border-subtle px-5 py-4 shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gold-400/12 border border-gold-400/20 flex items-center justify-center">
            <span className="text-gold-400 text-sm font-bold">A</span>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-white">Able</h3>
            <p className="text-[11px] text-text-muted">Studio operator chat</p>
          </div>
          {isLoading && <Loader className="w-3.5 h-3.5 text-gold-400 animate-spin ml-auto" />}
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {messages.length === 0 && (
          <div className="text-center text-text-muted text-xs pt-8 space-y-2">
            <p>Ask about system status, routing, or budgets.</p>
            <p className="text-text-muted/60">
              Full tool execution lives in{' '}
              <code className="bg-white/10 px-1 rounded">able chat</code>.
            </p>
          </div>
        )}

        {messages.map((msg) => {
          const text = msg.parts
            ?.filter((p: { type: string }) => p.type === 'text')
            .map((p: { type: string; text?: string }) => p.text ?? '')
            .join('') ?? '';

          return (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[85%] px-3.5 py-2.5 rounded-xl text-[13px] leading-relaxed whitespace-pre-wrap ${
                  msg.role === 'user'
                    ? 'bg-gold-600/20 border border-gold-600/30 text-text-primary'
                    : 'bg-white/5 border border-border-subtle text-text-secondary'
                }`}
              >
                {text}
              </div>
            </div>
          );
        })}

        {error && (
          <div className="text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-lg px-3 py-2">
            {error.message}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="border-t border-border-subtle px-4 py-3 shrink-0">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask Able anything..."
            disabled={isLoading}
            rows={1}
            className="flex-1 bg-white/5 border border-border-subtle rounded-lg px-3 py-2 text-[13px] text-text-primary placeholder:text-text-muted focus:outline-none focus:border-gold-400/50 disabled:opacity-50 resize-none"
          />
          <button
            onClick={submit}
            disabled={isLoading || !input.trim()}
            className="w-9 h-9 rounded-lg bg-gold-600 text-black flex items-center justify-center hover:bg-gold-500 transition-colors disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
