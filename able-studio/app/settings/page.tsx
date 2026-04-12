"use client";

import { useEffect, useState } from "react";

interface ToolCatalogEntry {
  name: string;
  toolName: string;
  displayName: string;
  description: string | null;
  category: string;
  enabled: boolean;
  requiresApproval: boolean;
  riskLevel: string;
  readOnly: boolean;
  concurrentSafe: boolean;
  surface: string;
  artifactKind: string;
  enabledByDefault: boolean;
  tags: string[];
}

const CATEGORY_ORDER = [
  "search-fetch",
  "execution",
  "agents-tasks",
  "planning",
  "mcp",
  "system",
  "experimental",
];

const CATEGORY_LABELS: Record<string, string> = {
  "search-fetch": "Search & Fetch",
  execution: "Execution",
  "agents-tasks": "Agents & Tasks",
  planning: "Planning",
  mcp: "MCP",
  system: "System",
  experimental: "Experimental",
};

const RISK_COLORS: Record<string, string> = {
  low: "badge-green",
  medium: "badge-orange",
  high: "badge-red",
};

import { fetcher } from "@/lib/fetcher";

export default function SettingsPage() {
  const [tools, setTools] = useState<ToolCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    fetcher("/api/settings")
      .then((data) => {
        if (!active) return;
        setTools(Array.isArray(data.catalog) ? data.catalog : []);
        setError(data.error || null);
      })
      .catch((err) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "Failed to load settings");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  async function toggleTool(toolName: string, field: "enabled" | "requiresApproval") {
    const existing = tools.find((tool) => tool.toolName === toolName);
    if (!existing) return;

    const nextValue = !existing[field];
    setSaving(toolName);
    setTools((current) =>
      current.map((tool) =>
        tool.toolName === toolName ? { ...tool, [field]: nextValue } : tool,
      ),
    );

    try {
      const response = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tool_name: toolName,
          enabled: field === "enabled" ? nextValue : existing.enabled,
          requires_approval:
            field === "requiresApproval" ? nextValue : existing.requiresApproval,
        }),
      });

      if (!response.ok) {
        throw new Error(`Failed to update ${toolName}`);
      }
    } catch (err) {
      setTools((current) =>
        current.map((tool) =>
          tool.toolName === toolName ? { ...tool, [field]: !nextValue } : tool,
        ),
      );
      setError(err instanceof Error ? err.message : "Failed to update tool");
    } finally {
      setSaving(null);
    }
  }

  const categories = [...new Set(tools.map((tool) => tool.category))].sort((a, b) => {
    const aIndex = CATEGORY_ORDER.indexOf(a);
    const bIndex = CATEGORY_ORDER.indexOf(b);
    if (aIndex === -1 && bIndex === -1) return a.localeCompare(b);
    if (aIndex === -1) return 1;
    if (bIndex === -1) return -1;
    return aIndex - bIndex;
  });

  const enabledCount = tools.filter((tool) => tool.enabled).length;
  const approvalCount = tools.filter((tool) => tool.requiresApproval).length;

  if (loading) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div className="h-8 w-56 skeleton" />
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[1, 2, 3].map((index) => (
            <div key={index} className="h-24 skeleton" />
          ))}
        </div>
        <div className="space-y-3">
          {[1, 2, 3, 4].map((index) => (
            <div key={index} className="h-28 skeleton" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      <div className="flex items-start justify-between gap-6 mb-8">
        <div>
          <h2 className="text-2xl font-semibold text-white mb-2">Tool Control Center</h2>
          <p className="text-white/40 max-w-3xl">
            Registry-backed tool policy for the gateway and studio. Disabled tools are
            removed from the runtime contract; approval toggles stay in the shared catalog.
          </p>
        </div>
        <div className="glass-card-elevated px-4 py-3 min-w-[240px]">
          <p className="text-[11px] uppercase tracking-[0.2em] text-text-muted mb-2">
            Catalog Summary
          </p>
          <div className="flex items-center justify-between text-sm text-text-secondary">
            <span>Enabled</span>
            <span className="text-gold-400 font-semibold">{enabledCount}</span>
          </div>
          <div className="flex items-center justify-between text-sm text-text-secondary mt-1.5">
            <span>Approval Gates</span>
            <span className="text-gold-400 font-semibold">{approvalCount}</span>
          </div>
          <div className="flex items-center justify-between text-sm text-text-secondary mt-1.5">
            <span>Total Tools</span>
            <span className="text-gold-400 font-semibold">{tools.length}</span>
          </div>
        </div>
      </div>

      {error && (
        <div className="glass-card border border-red-500/30 px-4 py-3 mb-6 text-sm text-red-300">
          {error}
        </div>
      )}

      {categories.map((category) => (
        <section key={category} className="mb-10">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-medium text-gold-400 uppercase tracking-wider">
              {CATEGORY_LABELS[category] || category}
            </h3>
            <span className="text-xs text-text-muted">
              {tools.filter((tool) => tool.category === category).length} tool
              {tools.filter((tool) => tool.category === category).length === 1 ? "" : "s"}
            </span>
          </div>

          <div className="space-y-3">
            {tools
              .filter((tool) => tool.category === category)
              .map((tool) => (
                <div
                  key={tool.toolName}
                  className="glass-card-elevated p-5 flex flex-col xl:flex-row xl:items-center xl:justify-between gap-5"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className={`status-dot ${tool.enabled ? "active" : "inactive"}`} />
                      <span className="font-medium text-white text-sm">{tool.displayName}</span>
                      <span className={`badge ${RISK_COLORS[tool.riskLevel] || "badge-orange"}`}>
                        {tool.riskLevel}
                      </span>
                      <span className="badge badge-blue">{tool.surface}</span>
                      <span className="badge badge-cyan">{tool.artifactKind}</span>
                      {tool.readOnly && <span className="badge badge-green">read-only</span>}
                      {!tool.concurrentSafe && (
                        <span className="badge badge-orange">serialized</span>
                      )}
                    </div>

                    {tool.description && (
                      <p className="text-sm text-text-secondary mt-2">{tool.description}</p>
                    )}

                    {tool.tags.length > 0 && (
                      <div className="flex items-center gap-2 flex-wrap mt-3">
                        {tool.tags.map((tag) => (
                          <span
                            key={`${tool.toolName}-${tag}`}
                            className="text-[11px] uppercase tracking-wide text-text-muted border border-border-subtle rounded-full px-2.5 py-1"
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-4 shrink-0">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <span className="text-xs text-white/40">Approval</span>
                      <button
                        onClick={() => toggleTool(tool.toolName, "requiresApproval")}
                        className={`w-11 h-6 rounded-full transition-colors relative ${
                          tool.requiresApproval ? "bg-gold-600" : "bg-white/10"
                        }`}
                        disabled={saving === tool.toolName}
                      >
                        <span
                          className={`absolute top-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                            tool.requiresApproval ? "left-[22px]" : "left-0.5"
                          }`}
                        />
                      </button>
                    </label>

                    <label className="flex items-center gap-2 cursor-pointer">
                      <span className="text-xs text-white/40">Enabled</span>
                      <button
                        onClick={() => toggleTool(tool.toolName, "enabled")}
                        className={`w-11 h-6 rounded-full transition-colors relative ${
                          tool.enabled ? "bg-green-600" : "bg-white/10"
                        }`}
                        disabled={saving === tool.toolName}
                      >
                        <span
                          className={`absolute top-0.5 w-5 h-5 bg-white rounded-full transition-transform ${
                            tool.enabled ? "left-[22px]" : "left-0.5"
                          }`}
                        />
                      </button>
                    </label>
                  </div>
                </div>
              ))}
          </div>
        </section>
      ))}
    </div>
  );
}
