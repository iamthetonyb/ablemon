"use client";

import { useState, useCallback } from "react";
import useSWR, { mutate } from "swr";
import { Settings, Shield, ShieldAlert, ShieldCheck, Loader2 } from "lucide-react";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const DEFAULT_TOOLS = [
  { toolName: "github_list_repos", displayName: "GitHub: List Repos", description: "Read-only listing of repositories", category: "github", enabled: true, requiresApproval: false, riskLevel: "low" },
  { toolName: "github_create_repo", displayName: "GitHub: Create Repo", description: "Create new repositories", category: "github", enabled: true, requiresApproval: true, riskLevel: "medium" },
  { toolName: "github_push_files", displayName: "GitHub: Push Files", description: "Push code to repositories", category: "github", enabled: true, requiresApproval: true, riskLevel: "medium" },
  { toolName: "github_create_pr", displayName: "GitHub: Create PR", description: "Open pull requests", category: "github", enabled: true, requiresApproval: true, riskLevel: "medium" },
  { toolName: "github_pages_deploy", displayName: "GitHub Pages: Deploy", description: "Deploy static sites to GitHub Pages", category: "deploy", enabled: true, requiresApproval: true, riskLevel: "low" },
  { toolName: "vercel_deploy", displayName: "Vercel: Deploy", description: "Deploy apps to Vercel free tier", category: "deploy", enabled: true, requiresApproval: true, riskLevel: "low" },
  { toolName: "do_list_droplets", displayName: "DigitalOcean: List Droplets", description: "Read-only droplet listing", category: "infra", enabled: true, requiresApproval: false, riskLevel: "low" },
  { toolName: "do_create_droplet", displayName: "DigitalOcean: Create Droplet", description: "Provision new VPS (billable)", category: "infra", enabled: true, requiresApproval: true, riskLevel: "high" },
];

const CAT_LABELS: Record<string, string> = { github: "GitHub", deploy: "Deployment", infra: "Infrastructure", ai: "AI Providers" };
const RISK_BADGE: Record<string, string> = { low: "badge-green", medium: "badge-orange", high: "badge-red" };
const RISK_ICON: Record<string, any> = { low: ShieldCheck, medium: Shield, high: ShieldAlert };

function mergeTools(defaults: typeof DEFAULT_TOOLS, serverTools: Record<string, any>) {
  return defaults.map((t) => ({
    ...t,
    enabled: serverTools[t.toolName]?.enabled ?? t.enabled,
    requiresApproval: serverTools[t.toolName]?.requires_approval ?? t.requiresApproval,
  }));
}

export default function SettingsPage() {
  const { data } = useSWR("/api/settings", fetcher);
  const [savingTool, setSavingTool] = useState<string | null>(null);

  const tools = data?.tools ? mergeTools(DEFAULT_TOOLS, data.tools) : DEFAULT_TOOLS;
  const categories = [...new Set(tools.map((t) => t.category))];

  const toggle = useCallback(async (toolName: string, field: "enabled" | "requiresApproval") => {
    const tool = tools.find((t) => t.toolName === toolName);
    if (!tool) return;
    setSavingTool(toolName);

    const newVal = !tool[field];
    const optimistic = {
      tools: Object.fromEntries(tools.map((t) => [
        t.toolName,
        {
          enabled: t.toolName === toolName && field === "enabled" ? newVal : t.enabled,
          requires_approval: t.toolName === toolName && field === "requiresApproval" ? newVal : t.requiresApproval,
          risk_level: t.riskLevel,
        },
      ])),
    };
    mutate("/api/settings", optimistic, false);

    try {
      await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tool_name: toolName,
          enabled: field === "enabled" ? newVal : tool.enabled,
          requires_approval: field === "requiresApproval" ? newVal : tool.requiresApproval,
        }),
      });
    } catch {
      mutate("/api/settings"); // Revert
    } finally {
      setSavingTool(null);
    }
  }, [tools]);

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-text-primary">Agent Controls</h2>
          <p className="text-text-secondary text-[14px] mt-1">
            Toggle MCP skills and tools. Disabled tools are physically removed from the agent&apos;s tool list.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Settings className="w-5 h-5 text-gold-400" aria-hidden="true" />
          <span className="text-[13px] text-text-secondary">{tools.filter((t) => t.enabled).length}/{tools.length} active</span>
        </div>
      </div>

      {categories.map((cat) => (
        <div key={cat} className="mb-8">
          <h3 className="text-[13px] font-semibold text-gold-400 uppercase tracking-wider mb-4">{CAT_LABELS[cat] || cat}</h3>
          <div className="space-y-3">
            {tools.filter((t) => t.category === cat).map((tool) => {
              const RiskIcon = RISK_ICON[tool.riskLevel || "medium"] || Shield;
              return (
                <div key={tool.toolName} className={`glass-card-elevated p-5 flex items-center justify-between gap-4 transition-all duration-200 ${!tool.enabled ? "opacity-50" : ""}`}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3">
                      <span className={`status-dot ${tool.enabled ? "status-dot-active" : "status-dot-inactive"}`} />
                      <span className="font-medium text-text-primary text-[14px]">{tool.displayName}</span>
                      <span className={`badge ${RISK_BADGE[tool.riskLevel || "medium"]}`}>
                        <RiskIcon className="w-3 h-3 mr-1" aria-hidden="true" />
                        {tool.riskLevel}
                      </span>
                      {savingTool === tool.toolName && <Loader2 className="w-4 h-4 text-gold-400 animate-spin" />}
                    </div>
                    {tool.description && <p className="text-[12px] text-text-muted mt-1 ml-5">{tool.description}</p>}
                  </div>

                  <div className="flex items-center gap-5 shrink-0">
                    <label className="flex items-center gap-2 cursor-pointer min-h-[44px]">
                      <span className="text-[12px] text-text-muted">Approval</span>
                      <div
                        className="toggle-track"
                        data-enabled={tool.requiresApproval}
                        onClick={() => toggle(tool.toolName, "requiresApproval")}
                        role="switch"
                        aria-checked={tool.requiresApproval}
                        aria-label={`Require approval for ${tool.displayName}`}
                      >
                        <div className="toggle-thumb" />
                      </div>
                    </label>

                    <label className="flex items-center gap-2 cursor-pointer min-h-[44px]">
                      <span className="text-[12px] text-text-muted">Enabled</span>
                      <div
                        className="toggle-track"
                        data-enabled={tool.enabled}
                        onClick={() => toggle(tool.toolName, "enabled")}
                        role="switch"
                        aria-checked={tool.enabled}
                        aria-label={`Enable ${tool.displayName}`}
                      >
                        <div className="toggle-thumb" />
                      </div>
                    </label>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
