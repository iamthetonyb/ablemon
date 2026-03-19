"use client";

import { useState, useCallback } from "react";
import useSWR, { mutate } from "swr";
import { FileText, Plus, Pin, Trash2, Save, Brain, ScrollText, Target, BookOpen } from "lucide-react";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const CATEGORIES = [
  { id: "", label: "All", icon: FileText },
  { id: "note", label: "Notes", icon: FileText },
  { id: "memory", label: "Memory", icon: Brain },
  { id: "learning", label: "Learnings", icon: BookOpen },
  { id: "insight", label: "Insights", icon: Target },
  { id: "briefing", label: "Briefings", icon: ScrollText },
];

const CAT_BADGE: Record<string, string> = {
  note: "badge-blue",
  memory: "badge-cyan",
  learning: "badge-purple",
  insight: "badge-gold",
  briefing: "badge-green",
};

export default function TrackingPage() {
  const [category, setCategory] = useState("");
  const { data } = useSWR(`/api/notes${category ? `?category=${category}` : ""}`, fetcher, { refreshInterval: 30000 });
  const [selectedDoc, setSelectedDoc] = useState<any>(null);
  const [editContent, setEditContent] = useState("");
  const [editTitle, setEditTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [newForm, setNewForm] = useState({ title: "", category: "note" });

  const notes = data?.notes || [];

  function openDoc(doc: any) {
    setSelectedDoc(doc);
    setEditContent(doc.content || "");
    setEditTitle(doc.title);
  }

  const saveDoc = useCallback(async () => {
    if (!selectedDoc) return;
    setSaving(true);
    const cacheKey = `/api/notes${category ? `?category=${category}` : ""}`;
    const updated = notes.map((d: any) => d.id === selectedDoc.id ? { ...d, title: editTitle, content: editContent } : d);
    mutate(cacheKey, { notes: updated }, false);
    await fetch("/api/notes", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: selectedDoc.id, title: editTitle, content: editContent }),
    });
    setSelectedDoc({ ...selectedDoc, title: editTitle, content: editContent });
    mutate(cacheKey);
    setSaving(false);
  }, [selectedDoc, editTitle, editContent, notes, category]);

  async function createDoc() {
    if (!newForm.title.trim()) return;
    const cacheKey = `/api/notes${category ? `?category=${category}` : ""}`;
    const opt = { id: "temp-" + Date.now(), title: newForm.title, content: "", category: newForm.category, pinned: false, source: "manual", createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() };
    mutate(cacheKey, { notes: [opt, ...notes] }, false);
    setShowNew(false);
    const saved = { ...newForm };
    setNewForm({ title: "", category: "note" });
    const res = await fetch("/api/notes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: saved.title, content: "", category: saved.category }) });
    const result = await res.json();
    mutate(cacheKey);
    if (result.note) openDoc(result.note);
  }

  async function deleteDoc(id: string) {
    const cacheKey = `/api/notes${category ? `?category=${category}` : ""}`;
    mutate(cacheKey, { notes: notes.filter((d: any) => d.id !== id) }, false);
    if (selectedDoc?.id === id) setSelectedDoc(null);
    await fetch(`/api/notes?id=${id}`, { method: "DELETE" });
    mutate(cacheKey);
  }

  async function togglePin(doc: any) {
    const cacheKey = `/api/notes${category ? `?category=${category}` : ""}`;
    mutate(cacheKey, { notes: notes.map((d: any) => d.id === doc.id ? { ...d, pinned: !d.pinned } : d) }, false);
    await fetch("/api/notes", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: doc.id, pinned: !doc.pinned }) });
    mutate(cacheKey);
  }

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-text-primary">Tracking & Memory</h2>
          <p className="text-text-secondary text-[14px] mt-1">Notes, learnings, agent memory, and briefings</p>
        </div>
        <button onClick={() => setShowNew(true)} className="btn-gold">
          <Plus className="w-4 h-4" aria-hidden="true" /> New Document
        </button>
      </div>

      {/* New doc form */}
      {showNew && (
        <div className="glass-card gold-glow p-5 mb-6 animate-fade-in">
          <div className="flex gap-3">
            <input autoFocus placeholder="Document title" value={newForm.title} onChange={(e) => setNewForm({ ...newForm, title: e.target.value })} onKeyDown={(e) => e.key === "Enter" && createDoc()} className="input-glass flex-1" />
            <select value={newForm.category} onChange={(e) => setNewForm({ ...newForm, category: e.target.value })} className="select-glass">
              <option value="note">Note</option>
              <option value="memory">Memory</option>
              <option value="learning">Learning</option>
              <option value="insight">Insight</option>
              <option value="briefing">Briefing</option>
            </select>
            <button onClick={createDoc} className="btn-gold">Create</button>
            <button onClick={() => setShowNew(false)} className="btn-ghost">Cancel</button>
          </div>
        </div>
      )}

      {/* Category filter */}
      <div className="flex gap-2 mb-6 flex-wrap">
        {CATEGORIES.map((c) => {
          const Icon = c.icon;
          return (
            <button
              key={c.id}
              onClick={() => { setCategory(c.id); setSelectedDoc(null); }}
              className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-[13px] font-medium transition-all duration-200 min-h-[44px] ${
                category === c.id ? "bg-gold-400/10 text-gold-400 border border-gold-400/20" : "text-text-secondary hover:text-text-primary bg-white/[0.03] border border-border-subtle"
              }`}
            >
              <Icon className="w-4 h-4" aria-hidden="true" />
              {c.label}
            </button>
          );
        })}
      </div>

      <div className="flex gap-6">
        {/* Document list */}
        <div className="w-[320px] shrink-0 space-y-2 max-h-[70vh] overflow-y-auto">
          {notes.length === 0 && (
            <div className="glass-card p-8 text-center">
              <ScrollText className="w-6 h-6 text-text-muted mx-auto mb-2" />
              <p className="text-text-muted text-[13px]">No documents yet.</p>
            </div>
          )}
          {[...notes].sort((a: any, b: any) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0)).map((doc: any) => (
            <button
              key={doc.id}
              onClick={() => openDoc(doc)}
              className={`glass-card p-4 w-full text-left transition-all duration-200 ${
                selectedDoc?.id === doc.id ? "border-gold-400/50 gold-glow" : ""
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <p className="text-[13px] font-medium text-text-primary truncate flex-1">{doc.title}</p>
                <div className="flex items-center gap-1 shrink-0 ml-2">
                  {doc.pinned && <Pin className="w-3 h-3 text-gold-400" />}
                  <span className={`badge ${CAT_BADGE[doc.category] || "badge-blue"}`}>{doc.category}</span>
                </div>
              </div>
              <p className="text-[11px] text-text-muted">{new Date(doc.updatedAt || doc.createdAt).toLocaleString()}</p>
            </button>
          ))}
        </div>

        {/* Editor */}
        <div className="flex-1">
          {selectedDoc ? (
            <div className="glass-card-elevated p-6 animate-slide-in">
              <div className="flex items-center justify-between mb-4">
                <input
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  className="text-lg font-bold text-text-primary bg-transparent border-none outline-none flex-1"
                />
                <div className="flex items-center gap-2 shrink-0">
                  <button onClick={() => togglePin(selectedDoc)} className="btn-ghost min-h-[36px] px-3" aria-label="Toggle pin">
                    <Pin className={`w-4 h-4 ${selectedDoc.pinned ? "text-gold-400" : ""}`} />
                  </button>
                  <button onClick={() => deleteDoc(selectedDoc.id)} className="btn-ghost min-h-[36px] px-3 hover:!border-error/30 hover:!text-error" aria-label="Delete">
                    <Trash2 className="w-4 h-4" />
                  </button>
                  <button onClick={saveDoc} disabled={saving} className="btn-gold min-h-[36px]">
                    <Save className="w-4 h-4" aria-hidden="true" />
                    {saving ? "Saving..." : "Save"}
                  </button>
                </div>
              </div>
              <textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                onKeyDown={(e) => { if (e.key === "s" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); saveDoc(); } }}
                className="markdown-editor"
                placeholder="Start writing... (Cmd+S to save)"
              />
            </div>
          ) : (
            <div className="glass-card p-12 text-center">
              <Brain className="w-8 h-8 text-text-muted mx-auto mb-3" />
              <p className="text-text-muted text-[14px]">Select a document or create a new one</p>
              <p className="text-text-muted text-[12px] mt-1">Supports markdown. Agent memory and briefings sync here.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
