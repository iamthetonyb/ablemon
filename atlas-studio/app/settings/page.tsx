"use client";

import { useState, useEffect } from "react";

interface ToolFlag {
  id: string;
  toolName: string;
  displayName: string;
  description: string | null;
  category: string;
  enabled: boolean;
  requiresApproval: boolean;
  riskLevel: string | null;
}

// Default tool definitions matching gateway.py ATLAS_TOOL_DEFS
const DEFAULT_TOOLS: Omit<ToolFlag, "id">[] = [
  { toolName: "github_list_repos", displayName: "GitHub: List Repos", description: "Read-only listing of repositories", category: "github", enabled: true, requiresApproval: false, riskLevel: "low" },
  { toolName: "github_create_repo", displayName: "GitHub: Create Repo", description: "Create new repositories", category: "github", enabled: true, requiresApproval: true, riskLevel: "medium" },
  { toolName: "github_push_files", displayName: "GitHub: Push Files", description: "Push code to repositories", category: "github", enabled: true, requiresApproval: true, riskLevel: "medium" },
  { toolName: "github_create_pr", displayName: "GitHub: Create PR", description: "Open pull requests", category: "github", enabled: true, requiresApproval: true, riskLevel: "medium" },
  { toolName: "github_pages_deploy", displayName: "GitHub Pages: Deploy", description: "Deploy static sites to GitHub Pages", category: "deploy", enabled: true, requiresApproval: true, riskLevel: "low" },
  { toolName: "vercel_deploy", displayName: "Vercel: Deploy", description: "Deploy apps to Vercel free tier", category: "deploy", enabled: true, requiresApproval: true, riskLevel: "low" },
  { toolName: "do_list_droplets", displayName: "DigitalOcean: List Droplets", description: "Read-only droplet listing", category: "infra", enabled: true, requiresApproval: false, riskLevel: "low" },
  { toolName: "do_create_droplet", displayName: "DigitalOcean: Create Droplet", description: "Provision new VPS (billable)", category: "infra", enabled: true, requiresApproval: true, riskLevel: "high" },
];

const CATEGORY_LABELS: Record<string, string> = {
  github: "GitHub",
  deploy: "Deployment",
  infra: "Infrastructure",
  ai: "AI Providers",
};

const RISK_COLORS: Record<string, string> = {
  low: "text-green-400",
  medium: "text-yellow-400",
  high: "text-red-400",
};

export default function SettingsPage() {
  const [tools, setTools] = useState<Omit<ToolFlag, "id">[]>(DEFAULT_TOOLS);
  const [saving, setSaving] = useState<string | null>(null);

  useEffect(() => {
    // Try to load from API, fall back to defaults
    fetch("/api/settings")
      .then((r) => r.json())
      .then((data) => {
        if (data.tools && Object.keys(data.tools).length > 0) {
          setTools((prev) =>
            prev.map((t) => ({
              ...t,
              enabled: data.tools[t.toolName]?.enabled ?? t.enabled,
              requiresApproval: data.tools[t.toolName]?.requires_approval ?? t.requiresApproval,
            }))
          );
        }
      })
      .catch(() => {}); // Use defaults on error
  }, []);

  async function toggleTool(toolName: string, field: "enabled" | "requiresApproval") {
    setSaving(toolName);
    const tool = tools.find((t) => t.toolName === toolName);
    if (!tool) return;

    const newValue = !tool[field];

    // Optimistic update
    setTools((prev) =>
      prev.map((t) => (t.toolName === toolName ? { ...t, [field]: newValue } : t))
    );

    try {
      await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tool_name: toolName,
          enabled: field === "enabled" ? newValue : tool.enabled,
          requires_approval: field === "requiresApproval" ? newValue : tool.requiresApproval,
        }),
      });
    } catch {
      // Revert on error
      setTools((prev) =>
        prev.map((t) => (t.toolName === toolName ? { ...t, [field]: !newValue } : t))
      );
    } finally {
      setSaving(null);
    }
  }

  const categories = [...new Set(tools.map((t) => t.category))];

  return (
    <div>
      <h2 className="text-2xl font-semibold text-white mb-2">Agent Controls</h2>
      <p className="text-white/40 mb-8">
        Toggle MCP skills and tools. Disabled tools are physically removed from the agent&apos;s
        tool list — it cannot call them.
      </p>

      {categories.map((cat) => (
        <div key={cat} className="mb-8">
          <h3 className="text-sm font-medium text-gold-400 uppercase tracking-wider mb-4">
            {CATEGORY_LABELS[cat] || cat}
          </h3>

          <div className="space-y-3">
            {tools
              .filter((t) => t.category === cat)
              .map((tool) => (
                <div
                  key={tool.toolName}
                  className="glass-card p-5 flex items-center justify-between gap-4"
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3">
                      <span
                        className={`status-dot ${tool.enabled ? "active" : "inactive"}`}
                      />
                      <span className="font-medium text-white text-sm">
                        {tool.displayName}
                      </span>
                      <span
                        className={`text-xs ${RISK_COLORS[tool.riskLevel || "medium"]}`}
                      >
                        {tool.riskLevel}
                      </span>
                    </div>
                    {tool.description && (
                      <p className="text-xs text-white/40 mt-1 ml-5">
                        {tool.description}
                      </p>
                    )}
                  </div>

                  <div className="flex items-center gap-4 shrink-0">
                    {/* Approval toggle */}
                    <label className="flex items-center gap-2 cursor-pointer">
                      <span className="text-xs text-white/40">Approval</span>
                      <button
                        onClick={() => toggleTool(tool.toolName, "requiresApproval")}
                        className={`w-9 h-5 rounded-full transition-colors relative ${
                          tool.requiresApproval ? "bg-gold-600" : "bg-white/10"
                        }`}
                        disabled={saving === tool.toolName}
                      >
                        <span
                          className={`absolute top-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
                            tool.requiresApproval ? "left-[18px]" : "left-0.5"
                          }`}
                        />
                      </button>
                    </label>

                    {/* Enabled toggle */}
                    <label className="flex items-center gap-2 cursor-pointer">
                      <span className="text-xs text-white/40">Enabled</span>
                      <button
                        onClick={() => toggleTool(tool.toolName, "enabled")}
                        className={`w-9 h-5 rounded-full transition-colors relative ${
                          tool.enabled ? "bg-green-600" : "bg-white/10"
                        }`}
                        disabled={saving === tool.toolName}
                      >
                        <span
                          className={`absolute top-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
                            tool.enabled ? "left-[18px]" : "left-0.5"
                          }`}
                        />
                      </button>
                    </label>
                  </div>
                </div>
              ))}
          </div>
        </div>
      ))}
    </div>
  );
}
