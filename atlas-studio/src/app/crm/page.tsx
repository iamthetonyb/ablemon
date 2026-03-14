"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { UserPlus, DollarSign, Plus, Mail, Phone, Building2, ArrowRight } from "lucide-react";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const CONTACT_STATUS = [
  { id: "lead", label: "Lead", class: "badge-blue" },
  { id: "prospect", label: "Prospect", class: "badge-gold" },
  { id: "customer", label: "Customer", class: "badge-green" },
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
  const { data: contactData } = useSWR("/api/contacts", fetcher);
  const { data: dealData } = useSWR("/api/deals", fetcher);
  const [tab, setTab] = useState<"contacts" | "deals">("contacts");
  const [showAddContact, setShowAddContact] = useState(false);
  const [showAddDeal, setShowAddDeal] = useState(false);
  const [contactForm, setContactForm] = useState({ name: "", email: "", phone: "", company: "", title: "", status: "lead" });
  const [dealForm, setDealForm] = useState({ title: "", contact_id: "", value_cents: 0, stage: "discovery", probability: 50 });
  const [selectedContact, setSelectedContact] = useState<any>(null);

  const contacts = contactData?.contacts || [];
  const deals = dealData?.deals || [];

  async function createContact() {
    if (!contactForm.name.trim()) return;
    const opt = { id: "temp-" + Date.now(), ...contactForm, tags: [], createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
    mutate("/api/contacts", { contacts: [opt, ...contacts] }, false);
    setShowAddContact(false);
    setContactForm({ name: "", email: "", phone: "", company: "", title: "", status: "lead" });
    await fetch("/api/contacts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(contactForm) });
    mutate("/api/contacts");
  }

  async function createDeal() {
    if (!dealForm.title.trim() || !dealForm.contact_id) return;
    const opt = { id: "temp-" + Date.now(), ...dealForm, valueCents: dealForm.value_cents, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
    mutate("/api/deals", { deals: [opt, ...deals] }, false);
    setShowAddDeal(false);
    setDealForm({ title: "", contact_id: "", value_cents: 0, stage: "discovery", probability: 50 });
    await fetch("/api/deals", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(dealForm) });
    mutate("/api/deals");
  }

  async function updateContactStatus(id: string, status: string) {
    const updated = contacts.map((c: any) => c.id === id ? { ...c, status } : c);
    mutate("/api/contacts", { contacts: updated }, false);
    await fetch("/api/contacts", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, status }) });
    mutate("/api/contacts");
  }

  async function moveDealStage(id: string, stage: string) {
    const closed_at = stage.startsWith("closed_") ? new Date().toISOString() : null;
    const updated = deals.map((d: any) => d.id === id ? { ...d, stage, closedAt: closed_at } : d);
    mutate("/api/deals", { deals: updated }, false);
    await fetch("/api/deals", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, stage, closed_at }) });
    mutate("/api/deals");
  }

  const totalPipeline = deals.filter((d: any) => !d.stage?.startsWith("closed_")).reduce((s: number, d: any) => s + (d.valueCents || 0), 0);

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-text-primary">CRM</h2>
          <p className="text-text-secondary text-[14px] mt-1">Contact management and deal pipeline tracking</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="glass-card px-4 py-2">
            <p className="text-[11px] text-text-muted">Pipeline Value</p>
            <p className="text-[16px] font-bold text-gold-400">${(totalPipeline / 100).toLocaleString()}</p>
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
                <input placeholder="Title / Role" value={contactForm.title} onChange={(e) => setContactForm({ ...contactForm, title: e.target.value })} className="input-glass" />
                <select value={contactForm.status} onChange={(e) => setContactForm({ ...contactForm, status: e.target.value })} className="select-glass">
                  {CONTACT_STATUS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                </select>
              </div>
              <div className="flex gap-2">
                <button onClick={createContact} className="btn-gold">Create</button>
                <button onClick={() => setShowAddContact(false)} className="btn-ghost">Cancel</button>
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
                  value={c.status}
                  onChange={(e) => updateContactStatus(c.id, e.target.value)}
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
                  <option value="">Select contact *</option>
                  {contacts.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <input type="number" placeholder="Value ($)" value={dealForm.value_cents / 100 || ""} onChange={(e) => setDealForm({ ...dealForm, value_cents: Math.round(Number(e.target.value) * 100) })} className="input-glass" />
              </div>
              <div className="flex gap-2">
                <button onClick={createDeal} className="btn-gold">Create</button>
                <button onClick={() => setShowAddDeal(false)} className="btn-ghost">Cancel</button>
              </div>
            </div>
          )}

          {/* Pipeline columns */}
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
                        <div className="flex gap-1 mt-2">
                          {DEAL_STAGES.filter((s) => s.id !== d.stage).slice(0, 2).map((s) => (
                            <button
                              key={s.id}
                              onClick={() => moveDealStage(d.id, s.id)}
                              className="text-[10px] px-2 py-1 rounded bg-white/[0.03] text-text-muted hover:text-text-primary transition-colors flex items-center gap-1"
                            >
                              <ArrowRight className="w-3 h-3" /> {s.label}
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
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
