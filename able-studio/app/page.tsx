"use client";

import useSWR from "swr";
import { useState, useCallback } from "react";
import { Activity, Briefcase, Zap, DollarSign, Database } from "lucide-react";
import { useLiveEvents, type GatewayEvent } from "@/lib/use-live-events";

import { fetcher } from "@/lib/fetcher";

const SEVERITY_COLORS: Record<string, string> = {
  info: "badge-blue",
  warning: "badge-orange",
  error: "badge-red",
  critical: "badge-red",
};

const STATUS_DOTS: Record<string, string> = {
  completed: "status-dot-active",
  running: "status-dot-warning",
  failed: "status-dot-error",
  blocked: "status-dot-inactive",
};

const PLAN_BADGE: Record<string, string> = {
  free: "badge-blue",
  pro: "badge-gold",
  enterprise: "badge-purple",
};

export default function DashboardPage() {
  const { data, error, isLoading, mutate } = useSWR("/api/dashboard", fetcher, { refreshInterval: 30000, errorRetryCount: 3 });
  const { data: corpusData } = useSWR("/api/metrics/corpus", fetcher, { refreshInterval: 60000, errorRetryCount: 3 });
  const { data: buddyData } = useSWR("/api/buddy", fetcher, { refreshInterval: 60000, errorRetryCount: 3 });
  const [liveEvents, setLiveEvents] = useState<GatewayEvent[]>([]);

  const handleLiveEvent = useCallback((event: GatewayEvent) => {
    if (event.type === "routing_decision") {
      // Refresh dashboard data when new interactions arrive
      mutate();
    }
    setLiveEvents((prev) => [event, ...prev].slice(0, 10));
  }, [mutate]);

  useLiveEvents(handleLiveEvent);

  if (isLoading) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div className="h-8 w-48 skeleton" />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => <div key={i} className="h-24 skeleton" />)}
        </div>
        <div className="h-64 skeleton" />
      </div>
    );
  }

  if (error || data?.error) {
    return (
      <div className="glass-card p-8 text-center">
        <p className="text-error text-sm mb-2">Failed to load dashboard</p>
        <p className="text-text-muted text-xs font-mono">{data?.error || error?.message}</p>
      </div>
    );
  }

  const m = data.metrics;
  const costDollars = (m.totalCostCents / 100).toFixed(2);
  const totalTokensK = ((m.totalInputTokens + m.totalOutputTokens) / 1000).toFixed(1);

  const corpusPairs = corpusData?.total_pairs ?? 0;
  const corpusPct = corpusData?.progress_pct ?? 0;

  const metrics = [
    { label: "Organizations", value: String(m.organizations), sub: "Active tenants", icon: Briefcase, border: "border-t-info" },
    { label: "Active Tools", value: String(m.enabledTools), sub: "MCP skills enabled", icon: Zap, border: "border-t-gold-400" },
    { label: "Audit Events", value: String(m.auditEvents24h), sub: "Last 24 hours", icon: Activity, border: "border-t-success" },
    { label: "Token Usage", value: `${totalTokensK}k`, sub: `$${costDollars} total cost`, icon: DollarSign, border: "border-t-warning" },
    { label: "Corpus Pairs", value: String(corpusPairs), sub: `${corpusPct}% to training threshold`, icon: Database, border: "border-t-purple-400" },
  ];

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-text-primary">Dashboard</h2>
          <p className="text-text-secondary text-[14px] mt-1">ABLE Swarm Control Plane</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="status-dot status-dot-active" />
          <span className="text-xs text-text-secondary">System Online</span>
        </div>
      </div>

      {/* Top metrics */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-4 mb-8">
        {metrics.map((metric) => {
          const Icon = metric.icon;
          return (
            <div key={metric.label} className={`glass-card-elevated p-5 border-t-2 ${metric.border}`}>
              <div className="flex items-center justify-between mb-3">
                <div className="p-2 bg-white/5 rounded-lg border border-border-subtle">
                  <Icon className="w-4 h-4 text-text-secondary" aria-hidden="true" />
                </div>
              </div>
              <p className="text-[11px] text-text-muted uppercase tracking-wider mb-1">{metric.label}</p>
              <p className="text-2xl font-bold text-gold-400">{metric.value}</p>
              <p className="text-xs text-text-muted mt-1">{metric.sub}</p>
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Recent Activity — 2 cols */}
        <div className="lg:col-span-2">
          <h3 className="text-[13px] font-semibold text-gold-400 uppercase tracking-wider mb-4">Recent Activity</h3>
          <div className="space-y-2">
            {data.recentLogs.length === 0 ? (
              <div className="glass-card p-8 text-center text-text-muted text-[13px]">
                No audit events yet. Activity will appear here once ABLE processes messages.
              </div>
            ) : (
              data.recentLogs.map((log: any) => (
                <div key={log.id} className="glass-card-elevated p-4 flex items-start gap-3">
                  <span className={`status-dot mt-1.5 ${STATUS_DOTS[log.status] || "status-dot-inactive"}`} />
                  <div className="flex-1 min-w-0">
                    <p className="text-[14px] text-text-primary truncate">{log.task}</p>
                    <div className="flex items-center gap-3 mt-1.5 flex-wrap">
                      <span className="text-[11px] text-text-muted uppercase font-mono">{log.agentRole}</span>
                      <span className={`badge ${SEVERITY_COLORS[log.severity] || "badge-blue"}`}>{log.severity}</span>
                      {log.providerUsed && <span className="text-[11px] text-text-muted">{log.providerUsed}</span>}
                      {log.durationMs > 0 && <span className="text-[11px] text-text-muted">{(log.durationMs / 1000).toFixed(1)}s</span>}
                      <span className="text-[11px] text-text-muted">
                        {new Date(log.createdAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                      </span>
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-[11px] text-text-muted font-mono">
                      {log.inputTokens + log.outputTokens > 0 ? `${((log.inputTokens + log.outputTokens) / 1000).toFixed(1)}k tok` : ""}
                    </p>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Client Pipeline — 1 col */}
        <div>
          <h3 className="text-[13px] font-semibold text-gold-400 uppercase tracking-wider mb-4">Client Pipeline</h3>
          <div className="space-y-2">
            {data.organizations.length === 0 ? (
              <div className="glass-card p-6 text-center text-text-muted text-[13px]">
                No clients yet.
                <a href="/clients" className="block mt-2 text-gold-400 hover:text-gold-300 text-xs">+ Add first client</a>
              </div>
            ) : (
              data.organizations.map((org: any) => (
                <div key={org.id} className="glass-card-elevated p-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-[14px] font-medium text-text-primary">{org.name}</p>
                      <p className="text-[11px] text-text-muted font-mono mt-0.5">{org.slug}</p>
                    </div>
                    <span className={`badge ${PLAN_BADGE[org.plan] || "badge-blue"}`}>{org.plan}</span>
                  </div>
                </div>
              ))
            )}
            <a href="/clients" className="block glass-card p-3 text-center text-xs text-gold-400/60 hover:text-gold-400 hover:border-gold-400/30 transition-colors">
              Manage Clients →
            </a>
          </div>

          {/* Live Events */}
          {liveEvents.length > 0 && (
            <>
              <h3 className="text-[13px] font-semibold text-gold-400 uppercase tracking-wider mb-4 mt-8">Live Events</h3>
              <div className="space-y-1.5">
                {liveEvents.map((ev, i) => (
                  <div key={i} className="glass-card p-2.5 flex items-center gap-2.5">
                    <span className="status-dot status-dot-active shrink-0" />
                    <div className="flex-1 min-w-0">
                      {ev.type === "routing_decision" && (
                        <p className="text-[12px] text-text-secondary truncate">
                          T{ev.tier} → <span className="text-text-primary">{ev.provider ?? ev.domain}</span>
                          {ev.score !== undefined && <span className="text-text-muted"> ({ev.score})</span>}
                        </p>
                      )}
                      {ev.type === "buddy_xp" && (
                        <p className="text-[12px] text-text-secondary truncate">
                          {ev.name} Lv{ev.level} • {ev.mood}
                        </p>
                      )}
                      {ev.type !== "routing_decision" && ev.type !== "buddy_xp" && (
                        <p className="text-[12px] text-text-secondary truncate">{ev.type}</p>
                      )}
                    </div>
                    <span className="text-[10px] text-text-muted shrink-0">
                      {ev.ts ? new Date(ev.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : ""}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Buddy Status */}
          <h3 className="text-[13px] font-semibold text-gold-400 uppercase tracking-wider mb-4 mt-8">Buddy</h3>
          {buddyData?.buddy ? (
            <a href="/buddy" className="block glass-card-elevated p-5 hover:border-gold-400/30 transition-colors mb-6">
              <div className="flex items-center justify-between mb-3">
                <div>
                  <p className="text-[14px] font-medium text-text-primary">{buddyData.buddy.name}</p>
                  <p className="text-[11px] text-text-muted capitalize">{buddyData.buddy.species} &middot; {buddyData.buddy.mood}</p>
                </div>
                <span className="text-[11px] font-mono text-gold-400">Lv {buddyData.buddy.level}</span>
              </div>
              <div className="w-full bg-white/5 rounded-full h-1.5">
                <div
                  className="bg-gold-400 h-1.5 rounded-full transition-all"
                  style={{ width: `${Math.min(100, (buddyData.buddy.xp / (buddyData.buddy.xp_to_next || 1)) * 100)}%` }}
                />
              </div>
              <div className="flex justify-between mt-1.5">
                <span className="text-[10px] text-text-muted">{buddyData.buddy.xp} XP</span>
                <span className="text-[10px] text-text-muted">{buddyData.buddy.xp_to_next} to next</span>
              </div>
            </a>
          ) : (
            <a href="/buddy" className="block glass-card p-5 text-center hover:border-gold-400/30 transition-colors mb-6">
              <p className="text-2xl mb-2">🥚</p>
              <p className="text-[13px] text-text-muted">No buddy active</p>
              <p className="text-[11px] text-text-muted mt-1">Start a chat to choose your starter</p>
            </a>
          )}

          {/* Quick Actions */}
          <h3 className="text-[13px] font-semibold text-gold-400 uppercase tracking-wider mb-4">Quick Actions</h3>
          <div className="space-y-2">
            <a href="/settings" className="block glass-card p-4 hover:border-gold-400/30 transition-colors">
              <p className="text-[14px] text-text-primary">Agent Controls</p>
              <p className="text-[11px] text-text-muted mt-0.5">Toggle tools & approval gates</p>
            </a>
            <a href="/crm" className="block glass-card p-4 hover:border-gold-400/30 transition-colors">
              <p className="text-[14px] text-text-primary">CRM Pipeline</p>
              <p className="text-[11px] text-text-muted mt-0.5">Contacts, deals, revenue tracking</p>
            </a>
            <a href="/tracking" className="block glass-card p-4 hover:border-gold-400/30 transition-colors">
              <p className="text-[14px] text-text-primary">Notes & Memory</p>
              <p className="text-[11px] text-text-muted mt-0.5">Learnings, insights, agent memory</p>
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
