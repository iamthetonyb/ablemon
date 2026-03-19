"use client";

import { useState, useEffect } from "react";

interface AuditEntry {
  id: string;
  runId: string;
  agentRole: string;
  task: string;
  content: string | null;
  thinkingSteps: any[] | null;
  toolCalls: any[] | null;
  providerUsed: string | null;
  modelUsed: string | null;
  inputTokens: number;
  outputTokens: number;
  costCents: number;
  durationMs: number;
  severity: string;
  status: string;
  createdAt: string;
}

const SEVERITY_STYLES: Record<string, string> = {
  info: "text-blue-400 bg-blue-400/10",
  warning: "text-yellow-400 bg-yellow-400/10",
  error: "text-red-400 bg-red-400/10",
  critical: "text-red-300 bg-red-500/20 font-bold",
};

const ROLE_STYLES: Record<string, string> = {
  scanner: "text-cyan-400",
  auditor: "text-purple-400",
  executor: "text-green-400",
  coordinator: "text-gold-400",
};

export default function AuditPage() {
  const [logs, setLogs] = useState<AuditEntry[]>([]);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [runDetails, setRunDetails] = useState<AuditEntry[]>([]);
  const [filters, setFilters] = useState({
    agentRole: "",
    severity: "",
  });

  useEffect(() => {
    const params = new URLSearchParams();
    if (filters.agentRole) params.set("agent_role", filters.agentRole);
    if (filters.severity) params.set("severity", filters.severity);

    fetch(`/api/audit?${params}`)
      .then((r) => r.json())
      .then((data) => setLogs(data.logs || []))
      .catch(() => {});
  }, [filters]);

  function openRunDetails(runId: string) {
    setSelectedRun(runId);
    fetch(`/api/audit?run_id=${runId}`)
      .then((r) => r.json())
      .then((data) => setRunDetails(data.logs || []))
      .catch(() => {});
  }

  return (
    <div>
      <h2 className="text-2xl font-semibold text-white mb-2">Audit Logs</h2>
      <p className="text-white/40 mb-6">
        Deep semantic log viewer — click any run to see internal thinking steps.
      </p>

      {/* Filters */}
      <div className="flex gap-4 mb-6">
        <select
          value={filters.agentRole}
          onChange={(e) => setFilters((f) => ({ ...f, agentRole: e.target.value }))}
          className="bg-surface-elevated border border-border-subtle rounded-lg px-3 py-2 text-sm text-white/70"
        >
          <option value="">All Roles</option>
          <option value="scanner">Scanner</option>
          <option value="auditor">Auditor</option>
          <option value="executor">Executor</option>
          <option value="coordinator">Coordinator</option>
        </select>

        <select
          value={filters.severity}
          onChange={(e) => setFilters((f) => ({ ...f, severity: e.target.value }))}
          className="bg-surface-elevated border border-border-subtle rounded-lg px-3 py-2 text-sm text-white/70"
        >
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
            <div className="glass-card p-8 text-center text-white/30">
              No audit logs yet. Logs appear when the Python gateway processes requests.
            </div>
          )}

          {logs.map((log) => (
            <button
              key={log.id}
              onClick={() => openRunDetails(log.runId)}
              className={`glass-card p-4 w-full text-left hover:border-gold-400/30 transition-colors ${
                selectedRun === log.runId ? "border-gold-400/50" : ""
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-3">
                  <span className={`text-xs font-mono ${ROLE_STYLES[log.agentRole] || "text-white/60"}`}>
                    {log.agentRole}
                  </span>
                  <span className={`text-xs px-2 py-0.5 rounded-full ${SEVERITY_STYLES[log.severity] || ""}`}>
                    {log.severity}
                  </span>
                  <span className="text-xs text-white/30 font-mono">{log.runId.slice(0, 12)}</span>
                </div>
                <span className="text-xs text-white/30">
                  {new Date(log.createdAt).toLocaleString()}
                </span>
              </div>

              <p className="text-sm text-white/70 truncate">{log.task}</p>

              <div className="flex gap-4 mt-2 text-xs text-white/30">
                {log.providerUsed && <span>{log.providerUsed}</span>}
                {log.inputTokens > 0 && <span>{log.inputTokens + log.outputTokens} tokens</span>}
                {log.durationMs > 0 && <span>{(log.durationMs / 1000).toFixed(1)}s</span>}
                {log.costCents > 0 && <span>${(log.costCents / 100).toFixed(4)}</span>}
              </div>
            </button>
          ))}
        </div>

        {/* Deep semantic viewer (right panel) */}
        {selectedRun && (
          <div className="w-[480px] shrink-0">
            <div className="glass-card gold-glow p-6 sticky top-8">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-medium text-gold-400">
                  Run: {selectedRun.slice(0, 16)}...
                </h3>
                <button
                  onClick={() => setSelectedRun(null)}
                  className="text-white/30 hover:text-white text-sm"
                >
                  Close
                </button>
              </div>

              <div className="space-y-4 max-h-[70vh] overflow-y-auto">
                {runDetails.map((entry, i) => (
                  <div key={entry.id} className="border-l-2 border-gold-400/30 pl-4">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-bold text-white/50">Step {i + 1}</span>
                      <span className={`text-xs ${ROLE_STYLES[entry.agentRole] || ""}`}>
                        {entry.agentRole}
                      </span>
                    </div>

                    <p className="text-sm text-white/70 mb-2">{entry.task}</p>

                    {/* Thinking steps */}
                    {entry.thinkingSteps && Array.isArray(entry.thinkingSteps) && (
                      <div className="mb-2">
                        <p className="text-xs text-purple-400 mb-1">Thinking:</p>
                        {entry.thinkingSteps.map((step: any, j: number) => (
                          <p key={j} className="text-xs text-white/40 ml-2 mb-0.5">
                            {typeof step === "string" ? step : JSON.stringify(step)}
                          </p>
                        ))}
                      </div>
                    )}

                    {/* Tool calls */}
                    {entry.toolCalls && Array.isArray(entry.toolCalls) && (
                      <div className="mb-2">
                        <p className="text-xs text-cyan-400 mb-1">Tool Calls:</p>
                        {entry.toolCalls.map((tc: any, j: number) => (
                          <div key={j} className="bg-white/5 rounded p-2 mb-1">
                            <p className="text-xs font-mono text-green-400">
                              {tc.name || tc.tool}
                            </p>
                            {tc.args && (
                              <pre className="text-xs text-white/30 mt-1 overflow-x-auto">
                                {JSON.stringify(tc.args, null, 2).slice(0, 500)}
                              </pre>
                            )}
                            {tc.result && (
                              <p className="text-xs text-white/40 mt-1 truncate">
                                → {typeof tc.result === "string" ? tc.result.slice(0, 200) : "OK"}
                              </p>
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Content/output */}
                    {entry.content && (
                      <div className="bg-white/5 rounded p-2">
                        <p className="text-xs text-white/50 whitespace-pre-wrap">
                          {entry.content.slice(0, 500)}
                          {entry.content.length > 500 && "..."}
                        </p>
                      </div>
                    )}
                  </div>
                ))}

                {runDetails.length === 0 && (
                  <p className="text-sm text-white/30">Loading run details...</p>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
