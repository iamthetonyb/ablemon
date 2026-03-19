"use client";

import { useState, useEffect } from "react";

interface Organization {
  id: string;
  name: string;
  slug: string;
  plan: string;
  createdAt: string;
}

export default function ClientsPage() {
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [newOrg, setNewOrg] = useState({ name: "", slug: "" });
  const [selectedOrg, setSelectedOrg] = useState<string | null>(null);
  const [keys, setKeys] = useState({ anthropic: "", openrouter: "", telegram: "" });
  const [savingKeys, setSavingKeys] = useState(false);

  useEffect(() => {
    fetch("/api/clients")
      .then((r) => r.json())
      .then((data) => setOrgs(data.organizations || []))
      .catch(() => {});
  }, []);

  async function createOrg() {
    const res = await fetch("/api/clients", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newOrg),
    });
    const data = await res.json().catch(() => ({ success: false, error: "Invalid response" }));
    if (data.success) {
      setOrgs((prev) => [...prev, data.organization]);
      setShowCreate(false);
      setNewOrg({ name: "", slug: "" });
    }
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
    } finally {
      setSavingKeys(false);
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-semibold text-white">Client Management</h2>
          <p className="text-white/40">Multi-tenant organizations and billing isolation</p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="px-4 py-2 bg-gold-600 text-black text-sm font-medium rounded-lg hover:bg-gold-500 transition-colors"
        >
          + New Client
        </button>
      </div>

      {/* Create org modal */}
      {showCreate && (
        <div className="glass-card gold-glow p-6 mb-6">
          <h3 className="text-sm font-medium text-gold-400 mb-4">New Organization</h3>
          <div className="flex gap-4">
            <input
              placeholder="Organization name"
              value={newOrg.name}
              onChange={(e) =>
                setNewOrg({
                  name: e.target.value,
                  slug: e.target.value.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, ""),
                })
              }
              className="flex-1 bg-white/5 border border-border-subtle rounded-lg px-3 py-2 text-sm text-white"
            />
            <input
              placeholder="slug"
              value={newOrg.slug}
              onChange={(e) => setNewOrg((p) => ({ ...p, slug: e.target.value }))}
              className="w-40 bg-white/5 border border-border-subtle rounded-lg px-3 py-2 text-sm text-white/60 font-mono"
            />
            <button
              onClick={createOrg}
              className="px-4 py-2 bg-gold-600 text-black text-sm font-medium rounded-lg"
            >
              Create
            </button>
            <button
              onClick={() => setShowCreate(false)}
              className="px-4 py-2 text-white/40 text-sm"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Org list */}
        <div className="space-y-3">
          {orgs.map((org) => (
            <button
              key={org.id}
              onClick={() => setSelectedOrg(org.id)}
              className={`glass-card p-5 w-full text-left hover:border-gold-400/30 transition-colors ${
                selectedOrg === org.id ? "border-gold-400/50" : ""
              }`}
            >
              <div className="flex items-center justify-between">
                <div>
                  <p className="font-medium text-white">{org.name}</p>
                  <p className="text-xs text-white/40 font-mono mt-1">{org.slug}</p>
                </div>
                <span className="text-xs px-2 py-1 rounded-full bg-gold-400/10 text-gold-400">
                  {org.plan}
                </span>
              </div>
            </button>
          ))}

          {orgs.length === 0 && (
            <div className="glass-card p-8 text-center text-white/30">
              No organizations yet. Create one to enable multi-tenant billing isolation.
            </div>
          )}
        </div>

        {/* Settings panel */}
        {selectedOrg && (
          <div className="glass-card gold-glow p-6">
            <h3 className="text-sm font-medium text-gold-400 mb-4">API Keys & Settings</h3>
            <p className="text-xs text-white/40 mb-6">
              Each client uses their own API keys. Costs are billed to their key, not yours.
            </p>

            <div className="space-y-4">
              <div>
                <label className="text-xs text-white/50 block mb-1">Anthropic API Key</label>
                <input
                  type="password"
                  placeholder="sk-ant-..."
                  value={keys.anthropic}
                  onChange={(e) => setKeys((k) => ({ ...k, anthropic: e.target.value }))}
                  className="w-full bg-white/5 border border-border-subtle rounded-lg px-3 py-2 text-sm text-white font-mono"
                />
              </div>

              <div>
                <label className="text-xs text-white/50 block mb-1">OpenRouter API Key</label>
                <input
                  type="password"
                  placeholder="sk-or-..."
                  value={keys.openrouter}
                  onChange={(e) => setKeys((k) => ({ ...k, openrouter: e.target.value }))}
                  className="w-full bg-white/5 border border-border-subtle rounded-lg px-3 py-2 text-sm text-white font-mono"
                />
              </div>

              <div>
                <label className="text-xs text-white/50 block mb-1">Telegram Bot Token</label>
                <input
                  type="password"
                  placeholder="123456:ABC-..."
                  value={keys.telegram}
                  onChange={(e) => setKeys((k) => ({ ...k, telegram: e.target.value }))}
                  className="w-full bg-white/5 border border-border-subtle rounded-lg px-3 py-2 text-sm text-white font-mono"
                />
              </div>

              <button
                onClick={saveApiKeys}
                disabled={savingKeys || (!keys.anthropic && !keys.openrouter && !keys.telegram)}
                className="w-full mt-2 px-4 py-2.5 bg-gold-600 text-black text-sm font-medium rounded-lg hover:bg-gold-500 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {savingKeys ? "Encrypting & Saving..." : "Save API Keys"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
