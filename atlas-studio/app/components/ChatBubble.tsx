'use client';

import { useState, useEffect, lazy, Suspense } from 'react';
import { MessageSquare, X } from 'lucide-react';

// Lazy-load the entire chat panel — zero SDK overhead until user clicks the bubble.
// useChat + DefaultChatTransport only initialize when the panel mounts.
const ChatPanel = lazy(() => import('./ChatPanel'));

export default function ChatBubble() {
  const [mounted, setMounted] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => setMounted(true), []);

  if (!mounted) return null;

  return (
    <>
      {/* Floating trigger — this is the only thing that renders on page load */}
      <button
        onClick={() => setOpen(!open)}
        className="fixed bottom-6 right-6 z-50 w-14 h-14 rounded-full flex items-center justify-center
          bg-gradient-to-br from-gold-400 to-gold-600 text-surface shadow-lg shadow-gold-400/25
          transition-all duration-200 hover:scale-105 active:scale-95"
        aria-label={open ? 'Close chat' : 'Open ATLAS chat'}
      >
        {open ? <X className="w-5 h-5" /> : <MessageSquare className="w-5 h-5" />}
      </button>

      {/* Chat panel — only loaded on first open, SDK initializes here */}
      {open && (
        <Suspense fallback={
          <div className="fixed bottom-24 right-6 z-50 w-[400px] h-[540px] flex items-center justify-center
            rounded-2xl border border-border-glass bg-surface-elevated/95 backdrop-blur-2xl shadow-2xl shadow-black/50">
            <div className="flex gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-gold-400 animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-gold-400 animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-gold-400 animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
          </div>
        }>
          <ChatPanel />
        </Suspense>
      )}
    </>
  );
}
