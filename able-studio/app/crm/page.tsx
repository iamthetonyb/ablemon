"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { UserPlus, DollarSign, Plus, Mail, Phone, Building2, ArrowRight } from "lucide-react";

import { fetcher } from "@/lib/fetcher";

const CONTACT_STATUS = [
  { id: "lead", label: "Lead", class: "badge-blue" },
  { id: "qualified", label: "Qualified", class: "badge-purple" },
  { id: "proposal", label: "Proposal", class: "badge-gold" },
  { id: "client", label: "Client", class: "badge-green" },
  { id: "churned", label: "Churned", class: "badge-red" },
];

const DEAL_STAGES = [
  { id: "discovery", label: "Discovery", class: "badge-blue" },
  { id: "proposal", label: "Proposal", class: "badge-gold" },
  { id: "negotiation", label: "Negotiation", class: "badge-orange" },
  { id: "closed_won", label: "Won", class: "badge-green" },
  { id: "closed_lost", label: "Lost", class: "badge-red" },
];

export default function CRMPage() {
  const { data: crmData } = useSWR("/api/crm", fetcher, { refreshInterval: 30000, errorRetryCount: 3 });
  const [tab, setTab] = useState<"contacts" | "deals">("contacts");
  const [showAddContact, setShowAddContact] = useState(false);
  const [showAddDeal, setShowAddDeal] = useState(false);
  const [contactForm, setContactForm] = useState({ name: "", email: "", phone: "", company: "", source: "" });
  const [dealForm, setDealForm] = useState({ title: "", contact_id: "", value: "", stage: "discovery" });

  const contacts = crmData?.contacts || [];
  const deals = crmData?.deals || [];

  async function createContact() {
    if (!contactForm.name.trim()) return;
    const opt = { id: "temp-" + Date.now(), ...contactForm, stage: "lead", createdAt: new Date().toISOString() };
    mutate("/api/crm", { ...crmData, contacts: [opt, ...contacts] }, false);
    setShowAddContact(false);
    const saved = { ...contactForm };
    setContactForm({ name: "", email: "", phone: "", company: "", source: "" });
    await fetch("/api/crm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "contact", ...saved }) });
    mutate("/api/crm");
  }

  async function createDeal() {
    if (!dealForm.title.trim()) return;
    const valueCents = Math.round(parseFloat(dealForm.value || "0") * 100);
    const opt = { id: "temp-" + Date.now(), title: dealForm.title, valueCents, stage: dealForm.stage, contactId: dealForm.contact_id || null, probability: 10, createdAt: new Date().toISOString() };
    mutate("/api/crm", { ...crmData, deals: [opt, ...deals] }, false);
    setShowAddDeal(false);
    const saved = { ...dealForm };
    setDealForm({ title: "", contact_id: "", value: "", stage: "discovery" });
    await fetch("/api/crm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "deal", title: saved.title, value_cents: valueCents, contact_id: saved.contact_id || null, stage: saved.stage }) });
    mutate("/api/crm");
  }

  async function updateContactStage(id: string, stage: string) {
    const updated = contacts.map((c: any) => c.id === id ? { ...c, stage } : c);
    mutate("/api/crm", { ...crmData, contacts: updated }, false);
    await fetch("/api/crm", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "contact", id, stage }) });
    mutate("/api/crm");
  }

  async function moveDealStage(id: string, stage: string) {
    const probMap: Record<string, number> = { discovery: 10, proposal: 30, negotiation: 60, closed_won: 100, closed_lost: 0 };
    const updated = deals.map((d: any) => d.id === id ? { ...d, stage, probability: probMap[stage] ?? d.probability } : d);
    mutate("/api/crm", { ...crmData, deals: updated }, false);
    await fetch("/api/crm", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "deal", id, stage, probability: probMap[stage] }) });
    mutate("/api/crm");
  }

  const totalPipeline = deals.filter((d: any) => !d.stage?.startsWith("closed")).reduce((s: number, d: any) => s + (d.valueCents || 0), 0);
  const wonRevenue = deals.filter((d: any) => d.stage === "closed_won").reduce((s: number, d: any) => s + (d.valueCents || 0), 0);

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-text-primary">CRM</h2>
          <p className="text-text-secondary text-[14px] mt-1">Contact management and deal pipeline</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="glass-card px-4 py-2">
            <p className="text-[11px] text-text-muted">Pipeline</p>
            <p className="text-[16px] font-bold text-gold-400">${(totalPipeline / 100).toLocaleString()}</p>
          </div>
          <div className="glass-card px-4 py-2">
            <p className="text-[11px] text-text-muted">Won</p>
            <p className="text-[16px] font-bold text-success">${(wonRevenue / 100).toLocaleString()}</p>
          </div>
        </div>
      </div>

      {/* Tab toggle */}
      <div className="flex gap-2 mb-6">
        <button onClick={() => setTab("contacts")} className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-[13px] font-medium min-h-[44px] transition-all duration-200 ${tab === "contacts" ? "bg-gold-400/10 text-gold-400 border border-gold-400/20" : "text-text-secondary bg-white/[0.03] border border-border-subtle"}`}>
          <UserPlus className="w-4 h-4" aria-hidden="true" /> Contacts ({contacts.length})
        </button>
        <button onClick={() => setTab("deals")} className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-[13px] font-medium min-h-[44px] transition-all duration-200 ${tab === "deals" ? "bg-gold-400/10 text-gold-400 border border-gold-400/20" : "text-text-secondary bg-white/[0.03] border border-border-subtle"}`}>
          <DollarSign className="w-4 h-4" aria-hidden="true" /> Deals ({deals.length})
        </button>
      </div>

      {/* ─── Contacts Tab ─────────────────────── */}
      {tab === "contacts" && (
        <>
          <div className="flex justify-end mb-4">
            <button onClick={() => setShowAddContact(true)} className="btn-gold">
              <Plus className="w-4 h-4" aria-hidden="true" /> Add Contact
            </button>
          </div>

          {showAddContact && (
            <div className="glass-card gold-glow p-5 mb-4 animate-fade-in">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
                <input placeholder="Full name *" value={contactForm.name} onChange={(e) => setContactForm({ ...contactForm, name: e.target.value })} className="input-glass" />
                <input placeholder="Email" value={contactForm.email} onChange={(e) => setContactForm({ ...contactForm, email: e.target.value })} className="input-glass" />
                <input placeholder="Phone" value={contactForm.phone} onChange={(e) => setContactForm({ ...contactForm, phone: e.target.value })} className="input-glass" />
                <input placeholder="Company" value={contactForm.company} onChange={(e) => setContactForm({ ...contactForm, company: e.target.value })} className="input-glass" />
                <select value={contactForm.source} onChange={(e) => setContactForm({ ...contactForm, source: e.target.value })} className="select-glass">
                  <option value="">Source</option>
                  <option value="referral">Referral</option>
                  <option value="inbound">Inbound</option>
                  <option value="outbound">Outbound</option>
                  <option value="organic">Organic</option>
                </select>
                <div className="flex gap-2 items-center">
                  <button onClick={createContact} className="btn-gold">Create</button>
                  <button onClick={() => setShowAddContact(false)} className="btn-ghost">Cancel</button>
                </div>
              </div>
            </div>
          )}

          <div className="space-y-2">
            {contacts.length === 0 && (
              <div className="glass-card p-8 text-center text-text-muted text-[13px]">No contacts yet. Add your first contact to start building your pipeline.</div>
            )}
            {contacts.map((c: any) => (
              <div key={c.id} className="glass-card-elevated p-4 flex items-center gap-4">
                <div className="w-10 h-10 rounded-full bg-gold-400/10 flex items-center justify-center text-gold-400 font-bold text-[14px] shrink-0">
                  {c.name?.charAt(0)?.toUpperCase() || "?"}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-[14px] font-medium text-text-primary">{c.name}</p>
                  <div className="flex items-center gap-3 mt-0.5 text-[12px] text-text-muted">
                    {c.company && <span className="flex items-center gap-1"><Building2 className="w-3 h-3" />{c.company}</span>}
                    {c.email && <span className="flex items-center gap-1"><Mail className="w-3 h-3" />{c.email}</span>}
                    {c.phone && <span className="flex items-center gap-1"><Phone className="w-3 h-3" />{c.phone}</span>}
                  </div>
                </div>
                <select
                  value={c.stage}
                  onChange={(e) => updateContactStage(c.id, e.target.value)}
                  className="select-glass text-[12px] py-1.5 w-auto"
                >
                  {CONTACT_STATUS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                </select>
              </div>
            ))}
          </div>
        </>
      )}

      {/* ─── Deals Tab (Pipeline) ─────────────── */}
      {tab === "deals" && (
        <>
          <div className="flex justify-end mb-4">
            <button onClick={() => setShowAddDeal(true)} className="btn-gold">
              <Plus className="w-4 h-4" aria-hidden="true" /> Add Deal
            </button>
          </div>

          {showAddDeal && (
            <div className="glass-card gold-glow p-5 mb-4 animate-fade-in">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
                <input placeholder="Deal title *" value={dealForm.title} onChange={(e) => setDealForm({ ...dealForm, title: e.target.value })} className="input-glass" />
                <select value={dealForm.contact_id} onChange={(e) => setDealForm({ ...dealForm, contact_id: e.target.value })} className="select-glass">
                  <option value="">Link to contact</option>
                  {contacts.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <input type="number" placeholder="Value ($)" value={dealForm.value} onChange={(e) => setDealForm({ ...dealForm, value: e.target.value })} className="input-glass" />
              </div>
              <div className="flex gap-2">
                <button onClick={createDeal} className="btn-gold">Create</button>
                <button onClick={() => setShowAddDeal(false)} className="btn-ghost">Cancel</button>
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            {DEAL_STAGES.map((stage) => {
              const stageDeals = deals.filter((d: any) => d.stage === stage.id);
              const stageValue = stageDeals.reduce((s: number, d: any) => s + (d.valueCents || 0), 0);
              return (
                <div key={stage.id} className="kanban-lane p-3">
                  <div className="flex items-center justify-between mb-3">
                    <span className={`badge ${stage.class}`}>{stage.label}</span>
                    <span className="text-[11px] text-text-muted">${(stageValue / 100).toLocaleString()}</span>
                  </div>
                  <div className="space-y-2">
                    {stageDeals.map((d: any) => (
                      <div key={d.id} className="glass-card p-3">
                        <p className="text-[13px] font-medium text-text-primary mb-1">{d.title}</p>
                        <p className="text-[12px] text-gold-400 font-medium">${((d.valueCents || 0) / 100).toLocaleString()}</p>
                        {d.contactId && (
                          <p className="text-[11px] text-text-muted mt-1">{contacts.find((c: any) => c.id === d.contactId)?.name}</p>
                        )}
                        <div className="flex gap-1 mt-2 flex-wrap">
                          {DEAL_STAGES.filter((s) => s.id !== d.stage).slice(0, 2).map((s) => (
                            <button key={s.id} onClick={() => moveDealStage(d.id, s.id)} className="text-[10px] px-2 py-1 rounded bg-white/[0.03] text-text-muted hover:text-text-primary transition-colors flex items-center gap-1">
                              <ArrowRight className="w-3 h-3" /> {s.label}
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
                    {stageDeals.length === 0 && (
                      <div className="border border-dashed border-white/10 rounded-xl p-6 text-center text-text-muted text-[11px]">Empty</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
