"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import { CalendarRange, Activity, Zap } from "lucide-react";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const SEVERITY_DOT: Record<string, string> = {
  info: "bg-info",
  warning: "bg-warning",
  error: "bg-error",
  critical: "bg-error",
};

const STATUS_LABEL: Record<string, string> = {
  completed: "Completed",
  running: "Running",
  failed: "Failed",
  blocked: "Blocked",
};

export default function TimelinePage() {
  const { data: auditData } = useSWR("/api/audit?limit=50", fetcher, { refreshInterval: 30000 });
  const { data: dashData } = useSWR("/api/dashboard", fetcher, { refreshInterval: 60000 });

  const logs = auditData?.logs || [];
  const metrics = dashData?.metrics;

  // Group logs by date
  const byDate: Record<string, any[]> = {};
  for (const log of logs) {
    const date = new Date(log.createdAt).toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric" });
    if (!byDate[date]) byDate[date] = [];
    byDate[date].push(log);
  }

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-text-primary">Timeline</h2>
          <p className="text-text-secondary text-[14px] mt-1">Chronological view of all ABLE activity and system events</p>
        </div>
        {metrics && (
          <div className="flex items-center gap-3">
            <div className="glass-card px-4 py-2">
              <p className="text-[11px] text-text-muted">Events (24h)</p>
              <p className="text-[16px] font-bold text-gold-400">{metrics.auditEvents24h}</p>
            </div>
            <div className="glass-card px-4 py-2">
              <p className="text-[11px] text-text-muted">Tokens Used</p>
              <p className="text-[16px] font-bold text-gold-400">{((metrics.totalInputTokens + metrics.totalOutputTokens) / 1000).toFixed(1)}k</p>
            </div>
          </div>
        )}
      </div>

      {logs.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <CalendarRange className="w-8 h-8 text-text-muted mx-auto mb-3" />
          <p className="text-text-muted text-[14px]">No activity yet. Events will appear here as ABLE processes requests.</p>
        </div>
      ) : (
        <div className="space-y-8 max-w-3xl">
          {Object.entries(byDate).map(([date, items]) => (
            <div key={date}>
              <div className="flex items-center gap-3 mb-4">
                <CalendarRange className="w-4 h-4 text-gold-400" aria-hidden="true" />
                <h3 className="text-[15px] font-semibold text-text-primary">{date}</h3>
                <span className="badge badge-gold">{items.length} events</span>
              </div>

              <div className="relative ml-5 border-l-2 border-gold-400/20 pl-6 space-y-3">
                {items.map((log: any) => (
                  <div key={log.id} className="relative">
                    {/* Timeline node */}
                    <div className="absolute -left-[31px] top-3 w-4 h-4 rounded-full bg-surface-elevated border-2 border-gold-400/40 flex items-center justify-center">
                      <div className={`w-2 h-2 rounded-full ${SEVERITY_DOT[log.severity] || "bg-info"}`} />
                    </div>

                    <div className="glass-card-elevated p-4">
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex-1 min-w-0">
                          <p className="text-[14px] font-medium text-text-primary">{log.task}</p>
                          <div className="flex items-center gap-3 mt-1.5 flex-wrap">
                            <span className="text-[12px] text-text-muted font-mono">{log.agentRole}</span>
                            <span className={`badge ${
                              log.severity === "error" || log.severity === "critical" ? "badge-red" :
                              log.severity === "warning" ? "badge-orange" : "badge-blue"
                            }`}>{log.severity}</span>
                            {log.providerUsed && <span className="text-[11px] text-text-muted">{log.providerUsed}</span>}
                            {log.status && <span className="text-[11px] text-text-muted">{STATUS_LABEL[log.status] || log.status}</span>}
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <p className="text-[12px] text-text-muted">
                            {new Date(log.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                          </p>
                          {(log.inputTokens + log.outputTokens) > 0 && (
                            <p className="text-[11px] text-text-muted font-mono mt-0.5">
                              {((log.inputTokens + log.outputTokens) / 1000).toFixed(1)}k tok
                            </p>
                          )}
                          {log.durationMs > 0 && (
                            <p className="text-[11px] text-text-muted font-mono">
                              {(log.durationMs / 1000).toFixed(1)}s
                            </p>
                          )}
                        </div>
                      </div>

                      {/* Content preview */}
                      {log.content && (
                        <div className="mt-3 bg-white/[0.02] rounded-lg p-3 border border-border-subtle">
                          <p className="text-[12px] text-text-secondary whitespace-pre-wrap line-clamp-3">{log.content}</p>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
