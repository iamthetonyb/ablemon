"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { CalendarRange, Plus, CheckCircle2, Circle, Flag } from "lucide-react";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

export default function TimelinePage() {
  const { data } = useSWR("/api/milestones", fetcher);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState({ title: "", target_date: "", phase: "", description: "" });
  const [view, setView] = useState<"roadmap" | "calendar">("roadmap");

  const milestones = data?.milestones || [];

  // Group by phase
  const phases = milestones.reduce((acc: Record<string, any[]>, m: any) => {
    const p = m.phase || "Unphased";
    if (!acc[p]) acc[p] = [];
    acc[p].push(m);
    return acc;
  }, {});

  // Group by month for calendar
  const byMonth = milestones.reduce((acc: Record<string, any[]>, m: any) => {
    const d = new Date(m.targetDate);
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    if (!acc[key]) acc[key] = [];
    acc[key].push(m);
    return acc;
  }, {});

  async function addMilestone() {
    if (!form.title || !form.target_date) return;
    const opt = { id: "temp-" + Date.now(), ...form, targetDate: form.target_date, completedAt: null, color: "#D4AF37", createdAt: new Date().toISOString() };
    mutate("/api/milestones", { milestones: [...milestones, opt] }, false);
    setShowAdd(false);
    setForm({ title: "", target_date: "", phase: "", description: "" });
    await fetch("/api/milestones", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(form) });
    mutate("/api/milestones");
  }

  async function toggleComplete(m: any) {
    const newVal = m.completedAt ? null : new Date().toISOString();
    const updated = milestones.map((ms: any) => ms.id === m.id ? { ...ms, completedAt: newVal } : ms);
    mutate("/api/milestones", { milestones: updated }, false);
    await fetch("/api/milestones", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: m.id, completed_at: newVal }) });
    mutate("/api/milestones");
  }

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-text-primary">Timeline</h2>
          <p className="text-text-secondary text-[14px] mt-1">Visual roadmap with phases, milestones, and target dates</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex rounded-lg overflow-hidden border border-border-subtle">
            <button
              onClick={() => setView("roadmap")}
              className={`px-4 py-2 text-[13px] font-medium min-h-[44px] transition-colors ${view === "roadmap" ? "bg-gold-400/10 text-gold-400" : "text-text-muted hover:text-text-secondary"}`}
            >
              Roadmap
            </button>
            <button
              onClick={() => setView("calendar")}
              className={`px-4 py-2 text-[13px] font-medium min-h-[44px] transition-colors ${view === "calendar" ? "bg-gold-400/10 text-gold-400" : "text-text-muted hover:text-text-secondary"}`}
            >
              Calendar
            </button>
          </div>
          <button onClick={() => setShowAdd(true)} className="btn-gold">
            <Plus className="w-4 h-4" aria-hidden="true" /> Add Milestone
          </button>
        </div>
      </div>

      {/* Add form */}
      {showAdd && (
        <div className="glass-card gold-glow p-5 mb-6 animate-fade-in">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
            <input placeholder="Milestone title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} className="input-glass" />
            <input type="date" value={form.target_date} onChange={(e) => setForm({ ...form, target_date: e.target.value })} className="input-glass" />
            <input placeholder="Phase (e.g. Phase 1, Sprint 3)" value={form.phase} onChange={(e) => setForm({ ...form, phase: e.target.value })} className="input-glass" />
            <input placeholder="Description (optional)" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="input-glass" />
          </div>
          <div className="flex gap-2">
            <button onClick={addMilestone} className="btn-gold">Create</button>
            <button onClick={() => setShowAdd(false)} className="btn-ghost">Cancel</button>
          </div>
        </div>
      )}

      {milestones.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <CalendarRange className="w-8 h-8 text-text-muted mx-auto mb-3" />
          <p className="text-text-muted text-[14px]">No milestones yet. Add one to build your project roadmap.</p>
        </div>
      ) : view === "roadmap" ? (
        /* Roadmap view — grouped by phase */
        <div className="space-y-8">
          {Object.entries(phases).map(([phase, items]) => (
            <div key={phase}>
              <div className="flex items-center gap-2 mb-4">
                <Flag className="w-4 h-4 text-gold-400" aria-hidden="true" />
                <h3 className="text-[15px] font-semibold text-text-primary">{phase}</h3>
                <span className="badge badge-gold">{(items as any[]).length}</span>
              </div>
              <div className="relative ml-5 border-l-2 border-gold-400/20 pl-6 space-y-4">
                {(items as any[]).map((m: any) => (
                  <div key={m.id} className="relative">
                    <div className="absolute -left-[31px] top-2 w-4 h-4 rounded-full bg-surface-elevated border-2 border-gold-400/40 flex items-center justify-center">
                      {m.completedAt && <div className="w-2 h-2 rounded-full bg-success" />}
                    </div>
                    <div className={`glass-card-elevated p-4 ${m.completedAt ? "opacity-60" : ""}`}>
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <button onClick={() => toggleComplete(m)} className="shrink-0" aria-label={m.completedAt ? "Mark incomplete" : "Mark complete"}>
                            {m.completedAt ? <CheckCircle2 className="w-5 h-5 text-success" /> : <Circle className="w-5 h-5 text-text-muted" />}
                          </button>
                          <div>
                            <p className={`text-[14px] font-medium ${m.completedAt ? "line-through text-text-muted" : "text-text-primary"}`}>{m.title}</p>
                            {m.description && <p className="text-[12px] text-text-muted mt-0.5">{m.description}</p>}
                          </div>
                        </div>
                        <span className="text-[12px] text-text-muted shrink-0">{new Date(m.targetDate).toLocaleDateString()}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        /* Calendar view — grouped by month */
        <div className="space-y-6">
          {Object.entries(byMonth).sort().map(([month, items]) => {
            const d = new Date(month + "-01");
            const label = d.toLocaleDateString("en-US", { year: "numeric", month: "long" });
            return (
              <div key={month}>
                <h3 className="text-[15px] font-semibold text-gold-400 mb-3">{label}</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                  {(items as any[]).map((m: any) => (
                    <div key={m.id} className={`glass-card p-4 ${m.completedAt ? "opacity-60" : ""}`}>
                      <div className="flex items-center gap-2 mb-2">
                        <button onClick={() => toggleComplete(m)} aria-label="Toggle completion">
                          {m.completedAt ? <CheckCircle2 className="w-4 h-4 text-success" /> : <Circle className="w-4 h-4 text-text-muted" />}
                        </button>
                        <p className={`text-[13px] font-medium ${m.completedAt ? "line-through text-text-muted" : "text-text-primary"}`}>{m.title}</p>
                      </div>
                      <div className="flex items-center justify-between text-[11px] text-text-muted">
                        <span>{m.phase || "No phase"}</span>
                        <span>{new Date(m.targetDate).toLocaleDateString()}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
