"use client";

import useSWR from "swr";
import {
  Zap,
  FolderKanban,
  CheckCircle2,
  Clock,
  TrendingUp,
  Activity,
  ArrowUpRight,
} from "lucide-react";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function DashboardPage() {
  const { data: metrics } = useSWR("/api/dashboard/metrics", fetcher, { refreshInterval: 15000 });
  const { data: activityData } = useSWR("/api/dashboard/activity", fetcher, { refreshInterval: 10000 });
  const { data: priorityData } = useSWR("/api/dashboard/priorities", fetcher, { refreshInterval: 30000 });

  const m = metrics || { activeProjects: 0, totalTasks: 0, completedToday: 0, upcomingDeadlines: 0, totalContacts: 0, openDeals: 0, recentAuditCount: 0, activeTools: 0 };
  const activities = activityData?.activities || [];
  const priorities = priorityData?.tasks || [];

  const CARDS = [
    { label: "Active Projects", value: m.activeProjects, icon: FolderKanban, color: "text-gold-400" },
    { label: "Completed Today", value: m.completedToday, icon: CheckCircle2, color: "text-success" },
    { label: "Upcoming Deadlines", value: m.upcomingDeadlines, icon: Clock, color: "text-warning" },
    { label: "Open Deals", value: m.openDeals, icon: TrendingUp, color: "text-info" },
  ];

  const PRI_LABEL = ["Low", "Medium", "High", "Urgent"];
  const PRI_CLASS = ["badge-blue", "badge-gold", "badge-orange", "badge-red"];
  const PRI_BORDER = ["priority-low", "priority-medium", "priority-high", "priority-urgent"];

  return (
    <div className="animate-fade-in">
      {/* Welcome */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-text-primary">Mission Control</h2>
          <p className="text-text-secondary text-[14px] mt-1">Real-time overview of your autonomous agent operations</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="status-dot status-dot-active" />
          <span className="text-[13px] text-text-secondary">All systems nominal</span>
        </div>
      </div>

      {/* Metric cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        {CARDS.map((c) => {
          const Icon = c.icon;
          return (
            <div key={c.label} className="glass-card-elevated p-5">
              <div className="flex items-center justify-between mb-3">
                <Icon className={`w-5 h-5 ${c.color}`} aria-hidden="true" />
                <ArrowUpRight className="w-4 h-4 text-text-muted" aria-hidden="true" />
              </div>
              <p className="text-2xl font-bold text-text-primary">{c.value}</p>
              <p className="text-[13px] text-text-secondary mt-1">{c.label}</p>
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        {/* Activity feed */}
        <div className="lg:col-span-3">
          <div className="glass-card p-6">
            <div className="flex items-center gap-2 mb-5">
              <Activity className="w-4 h-4 text-gold-400" aria-hidden="true" />
              <h3 className="text-[15px] font-semibold text-text-primary">Activity Feed</h3>
            </div>
            {activities.length === 0 ? (
              <p className="text-[13px] text-text-muted py-8 text-center">No recent activity. Agent operations will appear here in real time.</p>
            ) : (
              <div className="space-y-3 max-h-[420px] overflow-y-auto">
                {activities.map((a: any) => (
                  <div key={a.id} className="flex items-start gap-3 p-3 rounded-lg hover:bg-white/[0.02] transition-colors duration-150">
                    <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 text-[11px] font-bold ${a.actorType === "agent" ? "bg-gold-400/15 text-gold-400" : a.actorType === "system" ? "bg-info/15 text-info" : "bg-white/10 text-text-secondary"}`}>
                      {a.actorType === "agent" ? "AI" : a.actorType === "system" ? "SY" : "U"}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-[13px] text-text-primary">
                        <span className="font-medium">{a.actorName || a.actorType}</span>{" "}
                        <span className="text-text-secondary">{a.action}</span>
                        {a.targetName && <span className="text-gold-400 ml-1">{a.targetName}</span>}
                      </p>
                      <p className="text-[11px] text-text-muted mt-0.5">{new Date(a.createdAt).toLocaleString()}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Priorities + stats */}
        <div className="lg:col-span-2">
          <div className="glass-card p-6">
            <div className="flex items-center gap-2 mb-5">
              <Zap className="w-4 h-4 text-gold-400" aria-hidden="true" />
              <h3 className="text-[15px] font-semibold text-text-primary">Top Priorities</h3>
            </div>
            {priorities.length === 0 ? (
              <p className="text-[13px] text-text-muted py-8 text-center">No priority tasks. Create tasks in Projects.</p>
            ) : (
              <div className="space-y-2">
                {priorities.map((t: any) => (
                  <div key={t.id} className={`p-3 rounded-lg bg-white/[0.02] ${PRI_BORDER[t.priority] || "priority-low"}`}>
                    <div className="flex items-center justify-between">
                      <p className="text-[13px] text-text-primary font-medium truncate">{t.title}</p>
                      <span className={`badge ${PRI_CLASS[t.priority] || "badge-blue"}`}>{PRI_LABEL[t.priority] || "Low"}</span>
                    </div>
                    {t.dueDate && <p className="text-[11px] text-text-muted mt-1">Due: {new Date(t.dueDate).toLocaleDateString()}</p>}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="glass-card p-5 mt-4">
            <h3 className="text-[13px] font-semibold text-text-secondary mb-3">System Stats</h3>
            <div className="space-y-2 text-[13px]">
              {[
                ["Total Contacts", m.totalContacts],
                ["Active Tools", m.activeTools],
                ["Audit Events (24h)", m.recentAuditCount],
                ["Total Tasks", m.totalTasks],
              ].map(([label, val]) => (
                <div key={label as string} className="flex justify-between">
                  <span className="text-text-muted">{label}</span>
                  <span className="text-text-primary font-medium">{val}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
