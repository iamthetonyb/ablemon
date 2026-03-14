"use client";

import { useState, useEffect } from "react";
import { format } from "date-fns";
import { Save, Terminal, Brain, HardDrive, Network } from "lucide-react";
import { motion } from "framer-motion";
import { clsx } from "clsx";

export default function Tracking() {
  const [note, setNote] = useState("");
  const [lastSaved, setLastSaved] = useState<Date | null>(null);
  const [isClient, setIsClient] = useState(false);

  // Auto-load
  useEffect(() => {
    setIsClient(true);
    const saved = localStorage.getItem("atlas_scratchpad");
    if (saved) {
      setNote(saved);
      const time = localStorage.getItem("atlas_scratchpad_time");
      if (time) setLastSaved(new Date(time));
    }
  }, []);

  // Auto-save on keystroke with slight debounce feel
  useEffect(() => {
    if (!isClient) return;
    
    const timeout = setTimeout(() => {
      localStorage.setItem("atlas_scratchpad", note);
      const now = new Date();
      localStorage.setItem("atlas_scratchpad_time", now.toISOString());
      setLastSaved(now);
    }, 500);

    return () => clearTimeout(timeout);
  }, [note, isClient]);

  const logs = [
    { time: "07:31:02", level: "INFO", source: "HybridMemory", msg: "Initialized zstandard compression context." },
    { time: "07:31:05", level: "REQ", source: "AnthropicProvider", msg: "POST /v1/messages HTTP/1.1" },
    { time: "07:31:06", level: "RES", source: "AnthropicProvider", msg: "200 OK | tokens=4092, ttl=184ms" },
    { time: "07:31:10", level: "PROC", source: "SkillOrchestrator", msg: "Parsed ToolCall: github_push_files" },
    { time: "07:35:22", level: "DB", source: "VectorStore", msg: "Upserted 14 semantic hooks spanning 2 documents." },
  ];

  if (!isClient) return null;

  return (
    <div className="h-[calc(100vh-140px)] flex flex-col lg:flex-row gap-6 animate-in fade-in duration-700">
      
      {/* Left Pane: Obsidian-like Scratchpad */}
      <div className="flex-1 glass-card flex flex-col overflow-hidden">
        <div className="p-4 border-b border-glass-border flex items-center justify-between bg-white/[0.02]">
          <div className="flex items-center gap-2">
            <Brain className="w-5 h-5 text-gold" />
            <h2 className="font-semibold text-white tracking-wide">Episodic Scratchpad</h2>
          </div>
          <div className="flex items-center gap-4 text-xs text-gray-400">
            <span>{note.length} chars</span>
            <div className="flex items-center gap-1">
              <Save className="w-3.5 h-3.5 text-gray-500" />
              {lastSaved ? format(lastSaved, "h:mm:ss a") : "Not saved"}
            </div>
          </div>
        </div>
        
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="# Internal Monologue & Semantic Drafts...\n\nStart typing to automatically save to localStorage."
          className="flex-grow w-full bg-transparent p-6 text-gray-300 font-mono text-sm leading-relaxed outline-none resize-none custom-scrollbar placeholder-gray-600 focus:ring-0"
          spellCheck={false}
        />
      </div>

      {/* Right Pane: Agent Telemetry & Working Memory */}
      <div className="w-full lg:w-96 flex flex-col gap-6">
        
        {/* Memory Layers UI */}
        <div className="glass-card p-5">
          <h2 className="font-semibold text-white mb-4 flex items-center gap-2">
            <HardDrive className="w-4 h-4 text-blue-400" /> Layer Status
          </h2>
          <div className="space-y-3">
            {[
              { label: "Short-Term Buffer", usage: "45%", color: "bg-blue-500" },
              { label: "Working Context", usage: "82%", color: "bg-gold" },
              { label: "Semantic DB (Vector)", usage: "12%", color: "bg-purple-500" },
            ].map(layer => (
              <div key={layer.label}>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-gray-400">{layer.label}</span>
                  <span className="text-gray-300">{layer.usage}</span>
                </div>
                <div className="w-full h-1.5 bg-black/50 rounded-full overflow-hidden">
                  <motion.div 
                    initial={{ width: 0 }}
                    animate={{ width: layer.usage }}
                    transition={{ duration: 1, delay: 0.2 }}
                    className={`h-full ${layer.color} shadow-[0_0_10px_currentColor]`} 
                  />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Live SDK Terminal */}
        <div className="flex-grow glass-card flex flex-col overflow-hidden bg-[#0A0A0A]">
          <div className="p-3 border-b border-white/5 flex items-center gap-2 bg-black/40">
            <Terminal className="w-4 h-4 text-green-400" />
            <h2 className="text-xs font-mono font-medium text-gray-300">ATLAS_EXECUTION_LOG</h2>
            <div className="ml-auto flex gap-1.5">
              <div className="w-2.5 h-2.5 rounded-full bg-red-500/50" />
              <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/50" />
              <div className="w-2.5 h-2.5 rounded-full bg-green-500/50" />
            </div>
          </div>
          <div className="p-4 flex-grow overflow-y-auto custom-scrollbar font-mono text-[11px] leading-tight space-y-2">
            {logs.map((log, i) => (
              <div key={i} className="flex gap-3 text-gray-400 hover:bg-white/5 p-1 rounded transition-colors break-all">
                <span className="text-gray-600 flex-shrink-0">{log.time}</span>
                <span className={clsx(
                  "font-bold flex-shrink-0 w-8",
                  log.level === "REQ" ? "text-blue-400" :
                  log.level === "RES" ? "text-green-400" : 
                  log.level === "PROC" ? "text-gold" : "text-purple-400"
                )}>
                  {log.level}
                </span>
                <span className="text-gray-500 flex-shrink-0">[{log.source}]</span>
                <span className="text-gray-300">{log.msg}</span>
              </div>
            ))}
            <div className="flex gap-3 text-gray-500">
              <span className="w-14" />
              <span className="animate-pulse">_ blinking</span>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
