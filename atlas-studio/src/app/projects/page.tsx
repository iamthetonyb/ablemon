"use client";

import { useState, useEffect } from "react";
import { Plus, MoreHorizontal, Clock, GripVertical } from "lucide-react";
import { clsx } from "clsx";

type Priority = "Low" | "Medium" | "High";
type Column = "Backlog" | "In Progress" | "Done";

interface Task {
  id: string;
  title: string;
  desc: string;
  priority: Priority;
  status: Column;
  date: string;
}

const INITIAL_TASKS: Task[] = [
  { id: "1", title: "AGI Redis Refactor", desc: "Swap asyncio queue for Redis streams to prevent crash resets.", priority: "High", status: "In Progress", date: "Mar 15, 2026" },
  { id: "2", title: "WorldCup '26 Ad Variants", desc: "Run framework permutations through Claude Opus.", priority: "Medium", status: "Backlog", date: "Mar 14, 2026" },
  { id: "3", title: "Setup Neon Database", desc: "Create neon.tech cluster for Studio.", priority: "High", status: "Done", date: "Mar 14, 2026" },
];

export default function Projects() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [isClient, setIsClient] = useState(false);

  useEffect(() => {
    setIsClient(true);
    const saved = localStorage.getItem("atlas_tasks");
    if (saved) {
      setTasks(JSON.parse(saved));
    } else {
      setTasks(INITIAL_TASKS);
      localStorage.setItem("atlas_tasks", JSON.stringify(INITIAL_TASKS));
    }
  }, []);

  useEffect(() => {
    if (isClient) {
      localStorage.setItem("atlas_tasks", JSON.stringify(tasks));
    }
  }, [tasks, isClient]);

  const addTask = (status: Column) => {
    const newTask: Task = {
      id: Math.random().toString(36).substr(2, 9),
      title: "New Task",
      desc: "Task description...",
      priority: "Medium",
      status,
      date: new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
    };
    setTasks([...tasks, newTask]);
  };

  const columns: Column[] = ["Backlog", "In Progress", "Done"];

  if (!isClient) return null; // Avoid hydration mismatch

  return (
    <div className="h-[calc(100vh-140px)] flex flex-col animate-in fade-in duration-700">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-white mb-1">Projects</h1>
          <p className="text-gray-400">Mission and Kanban Tracking</p>
        </div>
        <button 
          onClick={() => addTask("Backlog")}
          className="px-4 py-2 bg-gold text-black hover:bg-gold/90 transition-colors rounded-md font-semibold text-sm shadow-[0_0_15px_var(--color-gold-glow)]"
        >
          Create Mission
        </button>
      </div>

      <div className="flex-grow grid grid-cols-1 md:grid-cols-3 gap-6 overflow-hidden">
        {columns.map(col => (
          <div key={col} className="glass-card flex flex-col h-full bg-white/[0.02]">
            {/* Column Header */}
            <div className="p-4 border-b border-glass-border flex items-center justify-between">
              <div className="flex items-center gap-2">
                <h2 className="font-semibold tracking-wide text-white">{col}</h2>
                <span className="bg-black/40 text-gray-400 text-xs px-2 py-0.5 rounded-full border border-glass-border">
                  {tasks.filter(t => t.status === col).length}
                </span>
              </div>
              <button onClick={() => addTask(col)} className="text-gray-400 hover:text-white transition-colors">
                <Plus className="w-5 h-5" />
              </button>
            </div>

            {/* Column Body */}
            <div className="p-4 flex-grow overflow-y-auto space-y-3 custom-scrollbar">
              {tasks.filter(t => t.status === col).map(task => (
                <div 
                  key={task.id}
                  className="bg-white/5 border border-glass-border rounded-xl p-4 hover:bg-white/10 transition-colors cursor-grab active:cursor-grabbing group"
                >
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex gap-2 items-center">
                      <GripVertical className="w-4 h-4 text-gray-500 opacity-0 group-hover:opacity-100 transition-opacity -ml-2" />
                      <span className={clsx(
                        "text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border",
                        task.priority === "High" ? "bg-red-500/10 text-red-400 border-red-500/20" :
                        task.priority === "Medium" ? "bg-blue-500/10 text-blue-400 border-blue-500/20" :
                        "bg-gray-500/10 text-gray-400 border-gray-500/20"
                      )}>
                        {task.priority}
                      </span>
                    </div>
                    <button className="text-gray-500 hover:text-white transition-colors">
                      <MoreHorizontal className="w-4 h-4" />
                    </button>
                  </div>
                  
                  <h3 className="font-semibold text-gray-200 mb-1 leading-snug">{task.title}</h3>
                  <p className="text-xs text-gray-400 mb-4 line-clamp-2">{task.desc}</p>
                  
                  <div className="flex items-center gap-1.5 text-xs text-gray-500 font-medium">
                    <Clock className="w-3.5 h-3.5" />
                    {task.date}
                  </div>
                </div>
              ))}
              
              {tasks.filter(t => t.status === col).length === 0 && (
                <div className="h-24 border-2 border-dashed border-white/10 rounded-xl flex items-center justify-center text-gray-500 text-sm">
                  Drop tasks here
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
