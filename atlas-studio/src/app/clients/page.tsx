"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { Users, Plus, Key, Loader2, Eye, EyeOff } from "lucide-react";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function ClientsPage() {
  const { data } = useSWR("/api/clients", fetcher);
  const [showCreate, setShowCreate] = useState(false);
  const [newOrg, setNewOrg] = useState({ name: "", slug: "" });
  const [selectedOrg, setSelectedOrg] = useState<string | null>(null);
  const [keys, setKeys] = useState({ anthropic: "", openrouter: "", telegram: "" });
  const [savingKeys, setSavingKeys] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showKey, setShowKey] = useState<Record<string, boolean>>({});

  const orgs = data?.organizations || [];

  async function createOrg() {
    if (!newOrg.name.trim()) return;
    const opt = { id: "temp-" + Date.now(), name: newOrg.name, slug: newOrg.slug, plan: "free", createdAt: new Date().toISOString() };
    mutate("/api/clients", { organizations: [...orgs, opt] }, false);
    setShowCreate(false);
    setNewOrg({ name: "", slug: "" });
    await fetch("/api/clients", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(newOrg) });
    mutate("/api/clients");
  }

  async function saveApiKeys() {
    if (!selectedOrg) return;
    setSavingKeys(true);
    try {
      await fetch("/api/clients", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          org_id: selectedOrg,
          ...(keys.anthropic && { anthropic_api_key: keys.anthropic }),
          ...(keys.openrouter && { openrouter_api_key: keys.openrouter }),
          ...(keys.telegram && { telegram_bot_token: keys.telegram }),
        }),
      });
      setKeys({ anthropic: "", openrouter: "", telegram: "" });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSavingKeys(false);
    }
  }

  const toggleShowKey = (key: string) => setShowKey((prev) => ({ ...prev, [key]: !prev[key] }));

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-text-primary">Client Management</h2>
          <p className="text-text-secondary text-[14px] mt-1">Multi-tenant organizations and billing isolation</p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-gold">
          <Plus className="w-4 h-4" aria-hidden="true" /> New Client
        </button>
      </div>

      {showCreate && (
        <div className="glass-card gold-glow p-5 mb-6 animate-fade-in">
          <h3 className="text-[13px] font-semibold text-gold-400 mb-3">New Organization</h3>
          <div className="flex gap-3">
            <input autoFocus placeholder="Organization name" value={newOrg.name} onChange={(e) => setNewOrg({ name: e.target.value, slug: e.target.value.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "") })} className="input-glass flex-1" />
            <input placeholder="slug" value={newOrg.slug} onChange={(e) => setNewOrg((p) => ({ ...p, slug: e.target.value }))} className="input-glass w-40" style={{ fontFamily: "monospace" }} />
            <button onClick={createOrg} className="btn-gold">Create</button>
            <button onClick={() => setShowCreate(false)} className="btn-ghost">Cancel</button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="space-y-3">
          {orgs.map((org: any) => (
            <button key={org.id} onClick={() => setSelectedOrg(org.id)} className={`glass-card-elevated p-5 w-full text-left transition-all duration-200 ${selectedOrg === org.id ? "border-gold-400/50 gold-glow" : ""}`}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-lg bg-gold-400/10 flex items-center justify-center">
                    <Users className="w-5 h-5 text-gold-400" aria-hidden="true" />
                  </div>
                  <div>
                    <p className="font-medium text-text-primary text-[14px]">{org.name}</p>
                    <p className="text-[12px] text-text-muted font-mono mt-0.5">{org.slug}</p>
                  </div>
                </div>
                <span className="badge badge-gold">{org.plan}</span>
              </div>
            </button>
          ))}
          {orgs.length === 0 && (
            <div className="glass-card p-8 text-center text-text-muted text-[13px]">No organizations yet. Create one to enable multi-tenant billing isolation.</div>
          )}
        </div>

        {selectedOrg && (
          <div className="animate-slide-in">
            <div className={`glass-card-elevated gold-glow p-6 ${saved ? "flash-success" : ""}`}>
              <div className="flex items-center gap-2 mb-5">
                <Key className="w-4 h-4 text-gold-400" aria-hidden="true" />
                <h3 className="text-[14px] font-semibold text-text-primary">API Keys & Settings</h3>
              </div>
              <p className="text-[12px] text-text-muted mb-5">Each client uses their own API keys. Costs are billed to their key, not yours.</p>

              <div className="space-y-4">
                {[
                  { key: "anthropic", label: "Anthropic API Key", placeholder: "sk-ant-..." },
                  { key: "openrouter", label: "OpenRouter API Key", placeholder: "sk-or-..." },
                  { key: "telegram", label: "Telegram Bot Token", placeholder: "123456:ABC-..." },
                ].map((field) => (
                  <div key={field.key}>
                    <label className="text-[12px] text-text-secondary font-medium block mb-1.5">{field.label}</label>
                    <div className="relative">
                      <input
                        type={showKey[field.key] ? "text" : "password"}
                        placeholder={field.placeholder}
                        value={(keys as any)[field.key]}
                        onChange={(e) => setKeys((k) => ({ ...k, [field.key]: e.target.value }))}
                        className="input-glass pr-10" style={{ fontFamily: "monospace", fontSize: "13px" }}
                      />
                      <button onClick={() => toggleShowKey(field.key)} className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-secondary transition-colors" aria-label={showKey[field.key] ? "Hide" : "Show"}>
                        {showKey[field.key] ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                      </button>
                    </div>
                  </div>
                ))}
                <button onClick={saveApiKeys} disabled={savingKeys || (!keys.anthropic && !keys.openrouter && !keys.telegram)} className="btn-gold w-full mt-2">
                  {savingKeys ? <><Loader2 className="w-4 h-4 animate-spin" /> Encrypting & Saving...</> : saved ? "Saved" : "Save API Keys"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
