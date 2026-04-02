'use client';

import { TerminalSquare, Wrench, Activity, Sparkles } from 'lucide-react';

const quickLinks = [
  {
    title: 'Tool-enabled chat',
    description: 'Use `able chat` for the full gateway, tools, approvals, and memory loop.',
    href: '/settings',
    icon: Wrench,
  },
  {
    title: 'Resource plane',
    description: 'Inspect models, services, storage, and lifecycle controls from the control plane.',
    href: '/resources',
    icon: Activity,
  },
  {
    title: 'Operator setup',
    description: 'Verify gateway, memory, Ollama, and service-token wiring before deeper runs.',
    href: '/setup',
    icon: Sparkles,
  },
];

export default function ChatPanel() {
  return (
    <div className="fixed bottom-24 right-6 z-50 w-[400px] h-[540px] rounded-2xl border border-border-glass bg-surface-elevated/95 backdrop-blur-2xl shadow-2xl shadow-black/50 overflow-hidden animate-slide-in">
      <div className="border-b border-border-subtle px-5 py-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gold-400/12 border border-gold-400/20 flex items-center justify-center">
            <TerminalSquare className="w-5 h-5 text-gold-400" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-white">ABLE Operator Panel</h3>
            <p className="text-xs text-text-muted mt-0.5">
              Studio keeps the control center light. Full tool-running chat still lives in the terminal runtime.
            </p>
          </div>
        </div>
      </div>

      <div className="p-5 h-[calc(100%-81px)] overflow-y-auto">
        <div className="glass-card p-4 mb-5">
          <p className="text-xs uppercase tracking-[0.2em] text-text-muted mb-2">
            Recommended Flow
          </p>
          <ol className="space-y-2 text-sm text-text-secondary">
            <li>1. Use Studio to inspect system state, resources, and approvals.</li>
            <li>2. Use <span className="text-gold-400 font-mono">able chat</span> for live tool work and distillation-rich transcripts.</li>
            <li>3. Return here to review control-plane artifacts and operator data.</li>
          </ol>
        </div>

        <div className="space-y-3">
          {quickLinks.map((item) => {
            const Icon = item.icon;
            return (
              <a
                key={item.title}
                href={item.href}
                className="glass-card-elevated p-4 block hover:border-gold-400/30 transition-colors"
              >
                <div className="flex items-start gap-3">
                  <div className="w-9 h-9 rounded-lg bg-white/5 border border-border-subtle flex items-center justify-center shrink-0">
                    <Icon className="w-4 h-4 text-gold-400" />
                  </div>
                  <div>
                    <p className="text-sm text-text-primary">{item.title}</p>
                    <p className="text-xs text-text-muted mt-1">{item.description}</p>
                  </div>
                </div>
              </a>
            );
          })}
        </div>

        <div className="glass-card p-4 mt-5">
          <p className="text-xs uppercase tracking-[0.2em] text-text-muted mb-2">
            Terminal Shortcuts
          </p>
          <pre className="text-xs text-text-secondary whitespace-pre-wrap break-words">
{`able
able chat --control-port 8080
/resources
/battle reasoning
/compact`}
          </pre>
        </div>
      </div>
    </div>
  );
}
