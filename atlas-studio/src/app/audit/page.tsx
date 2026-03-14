"use client";

import { useState } from "react";
import useSWR from "swr";
import { ScrollText, ChevronDown, ChevronRight, X } from "lucide-react";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const SEVERITY_BADGE: Record<string, string> = { info: "badge-blue", warning: "badge-orange", error: "badge-red", critical: "badge-red" };
const ROLE_COLOR: Record<string, string> = { scanner: "text-cyan-400", auditor: "text-purple-400", executor: "text-green-400", coordinator: "text-gold-400" };

export default function AuditPage() {
  const [filters, setFilters] = useState({ agentRole: "", severity: "" });
  const [selectedRun, setSelectedRun] = useState<string | null>(null);

  const params = new URLSearchParams();
  if (filters.agentRole) params.set("agent_role", filters.agentRole);
  if (filters.severity) params.set("severity", filters.severity);

  const { data: logData } = useSWR(`/api/audit?${params}`, fetcher, { refreshInterval: 8000 });
  const { data: runData } = useSWR(selectedRun ? `/api/audit?run_id=${selectedRun}` : null, fetcher);

  const logs = logData?.logs || [];
  const runDetails = runData?.logs || [];

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-text-primary">Audit Logs</h2>
          <p className="text-text-secondary text-[14px] mt-1">Deep semantic log viewer — click any run to see internal thinking steps</p>
        </div>
        <div className="flex items-center gap-2">
          <ScrollText className="w-5 h-5 text-gold-400" aria-hidden="true" />
          <span className="text-[13px] text-text-secondary">{logs.length} entries</span>
        </div>
      </div>

      {/* Filters */}
      <div className="flex gap-3 mb-6">
        <select value={filters.agentRole} onChange={(e) => setFilters((f) => ({ ...f, agentRole: e.target.value }))} className="select-glass" aria-label="Filter by agent role">
          <option value="">All Roles</option>
          <option value="scanner">Scanner</option>
          <option value="auditor">Auditor</option>
          <option value="executor">Executor</option>
          <option value="coordinator">Coordinator</option>
        </select>
        <select value={filters.severity} onChange={(e) => setFilters((f) => ({ ...f, severity: e.target.value }))} className="select-glass" aria-label="Filter by severity">
          <option value="">All Severities</option>
          <option value="info">Info</option>
          <option value="warning">Warning</option>
          <option value="error">Error</option>
          <option value="critical">Critical</option>
        </select>
      </div>

      <div className="flex gap-6">
        {/* Log list */}
        <div className="flex-1 space-y-2">
          {logs.length === 0 && (
            <div className="glass-card p-12 text-center">
              <ScrollText className="w-8 h-8 text-text-muted mx-auto mb-3" />
              <p className="text-text-muted text-[14px]">No audit logs yet. Logs appear when the Python gateway processes requests.</p>
            </div>
          )}
          {logs.map((log: any) => (
            <button
              key={log.id}
              onClick={() => setSelectedRun(log.runId)}
              className={`glass-card-elevated p-4 w-full text-left transition-all duration-200 ${selectedRun === log.runId ? "border-gold-400/50 gold-glow" : ""}`}
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3">
                  <span className={`text-[12px] font-mono font-bold ${ROLE_COLOR[log.agentRole] || "text-text-secondary"}`}>{log.agentRole}</span>
                  <span className={`badge ${SEVERITY_BADGE[log.severity] || "badge-blue"}`}>{log.severity}</span>
                  <span className="text-[11px] text-text-muted font-mono">{log.runId?.slice(0, 12)}</span>
                </div>
                <span className="text-[11px] text-text-muted">{new Date(log.createdAt).toLocaleString()}</span>
              </div>
              <p className="text-[13px] text-text-secondary truncate">{log.task}</p>
              <div className="flex gap-4 mt-2 text-[11px] text-text-muted">
                {log.providerUsed && <span>{log.providerUsed}</span>}
                {log.inputTokens > 0 && <span>{log.inputTokens + log.outputTokens} tokens</span>}
                {log.durationMs > 0 && <span>{(log.durationMs / 1000).toFixed(1)}s</span>}
                {log.costCents > 0 && <span>${(log.costCents / 100).toFixed(4)}</span>}
              </div>
            </button>
          ))}
        </div>

        {/* Deep semantic viewer */}
        {selectedRun && (
          <div className="w-[480px] shrink-0 animate-slide-in">
            <div className="glass-card-elevated gold-glow p-6 sticky top-8">
              <div className="flex items-center justify-between mb-5">
                <h3 className="text-[14px] font-semibold text-gold-400">Run: {selectedRun.slice(0, 16)}...</h3>
                <button onClick={() => setSelectedRun(null)} className="btn-ghost min-h-[36px] px-3" aria-label="Close panel">
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="space-y-4 max-h-[70vh] overflow-y-auto">
                {runDetails.map((entry: any, i: number) => (
                  <RunStep key={entry.id} entry={entry} step={i + 1} />
                ))}
                {runDetails.length === 0 && <p className="text-[13px] text-text-muted">Loading run details...</p>}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function RunStep({ entry, step }: { entry: any; step: number }) {
  const [showThinking, setShowThinking] = useState(false);
  const [showTools, setShowTools] = useState(false);

  return (
    <div className="border-l-2 border-gold-400/30 pl-4">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[11px] font-bold text-text-muted">Step {step}</span>
        <span className={`text-[11px] font-bold ${ROLE_COLOR[entry.agentRole] || "text-text-secondary"}`}>{entry.agentRole}</span>
      </div>
      <p className="text-[13px] text-text-secondary mb-2">{entry.task}</p>

      {entry.thinkingSteps && Array.isArray(entry.thinkingSteps) && entry.thinkingSteps.length > 0 && (
        <div className="mb-2">
          <button onClick={() => setShowThinking(!showThinking)} className="flex items-center gap-1 text-[11px] text-purple-400 hover:text-purple-300 transition-colors min-h-[32px]">
            {showThinking ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            Thinking ({entry.thinkingSteps.length} steps)
          </button>
          {showThinking && (
            <div className="mt-1 ml-4 space-y-0.5 animate-fade-in">
              {entry.thinkingSteps.map((s: any, j: number) => (
                <p key={j} className="text-[11px] text-text-muted">{typeof s === "string" ? s : JSON.stringify(s)}</p>
              ))}
            </div>
          )}
        </div>
      )}

      {entry.toolCalls && Array.isArray(entry.toolCalls) && entry.toolCalls.length > 0 && (
        <div className="mb-2">
          <button onClick={() => setShowTools(!showTools)} className="flex items-center gap-1 text-[11px] text-cyan-400 hover:text-cyan-300 transition-colors min-h-[32px]">
            {showTools ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            Tool Calls ({entry.toolCalls.length})
          </button>
          {showTools && (
            <div className="mt-1 space-y-1 animate-fade-in">
              {entry.toolCalls.map((tc: any, j: number) => (
                <div key={j} className="bg-white/[0.03] rounded-lg p-2">
                  <p className="text-[11px] font-mono text-green-400">{tc.name || tc.tool}</p>
                  {tc.args && <pre className="text-[10px] text-text-muted mt-1 overflow-x-auto">{JSON.stringify(tc.args, null, 2).slice(0, 500)}</pre>}
                  {tc.result && <p className="text-[10px] text-text-muted mt-1 truncate">→ {typeof tc.result === "string" ? tc.result.slice(0, 200) : "OK"}</p>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {entry.content && (
        <div className="bg-white/[0.03] rounded-lg p-2">
          <p className="text-[11px] text-text-secondary whitespace-pre-wrap">{entry.content.slice(0, 500)}{entry.content.length > 500 && "..."}</p>
        </div>
      )}
    </div>
  );
}
