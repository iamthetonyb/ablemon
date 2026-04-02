"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { Plus, GripVertical, Trash2 } from "lucide-react";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const LANES = [
  { id: "backlog", label: "Backlog", color: "text-text-muted" },
  { id: "in_progress", label: "In Progress", color: "text-gold-400" },
  { id: "review", label: "Review", color: "text-info" },
  { id: "done", label: "Done", color: "text-success" },
];

const PRI_LABEL: Record<string, string> = { low: "Low", medium: "Medium", high: "High", urgent: "Urgent" };
const PRI_CLASS: Record<string, string> = { low: "badge-blue", medium: "badge-gold", high: "badge-orange", urgent: "badge-red" };
const PRI_BORDER: Record<string, string> = { low: "priority-low", medium: "priority-medium", high: "priority-high", urgent: "priority-urgent" };

export default function ProjectsPage() {
  const { data } = useSWR("/api/tasks", fetcher, { refreshInterval: 30000 });
  const [showNewTask, setShowNewTask] = useState<string | null>(null);
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [newTaskPriority, setNewTaskPriority] = useState("medium");
  const [newTaskAssignee, setNewTaskAssignee] = useState("");
  const [dragItem, setDragItem] = useState<string | null>(null);

  const tasks = data?.tasks || [];

  async function createTask(lane: string) {
    if (!newTaskTitle.trim()) return;
    const opt = { id: "temp-" + Date.now(), title: newTaskTitle, status: lane, priority: newTaskPriority, assignee: newTaskAssignee || null, description: null, tags: null, createdAt: new Date().toISOString() };
    mutate("/api/tasks", { ...data, tasks: [...tasks, opt] }, false);
    const saved = { title: newTaskTitle, priority: newTaskPriority, assignee: newTaskAssignee };
    setNewTaskTitle("");
    setShowNewTask(null);
    setNewTaskPriority("medium");
    setNewTaskAssignee("");
    await fetch("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: saved.title, status: lane, priority: saved.priority, assignee: saved.assignee || null }) });
    mutate("/api/tasks");
  }

  async function moveTask(taskId: string, newStatus: string) {
    const updated = tasks.map((t: any) => t.id === taskId ? { ...t, status: newStatus } : t);
    mutate("/api/tasks", { ...data, tasks: updated }, false);
    await fetch("/api/tasks", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: taskId, status: newStatus }) });
    mutate("/api/tasks");
  }

  async function deleteTask(taskId: string) {
    mutate("/api/tasks", { ...data, tasks: tasks.filter((t: any) => t.id !== taskId) }, false);
    await fetch(`/api/tasks?id=${taskId}`, { method: "DELETE" });
    mutate("/api/tasks");
  }

  const totalTasks = tasks.length;
  const doneTasks = tasks.filter((t: any) => t.status === "done").length;

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-text-primary">Projects</h2>
          <p className="text-text-secondary text-[14px] mt-1">Kanban task management — {doneTasks}/{totalTasks} completed</p>
        </div>
        <button onClick={() => setShowNewTask("backlog")} className="btn-gold" aria-label="Create new task">
          <Plus className="w-4 h-4" aria-hidden="true" /> New Task
        </button>
      </div>

      {/* Kanban board */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        {LANES.map((lane) => {
          const laneTasks = tasks.filter((t: any) => t.status === lane.id);
          return (
            <div
              key={lane.id}
              className="kanban-lane p-4"
              onDragOver={(e) => { e.preventDefault(); e.currentTarget.setAttribute("data-drag-over", "true"); }}
              onDragLeave={(e) => e.currentTarget.setAttribute("data-drag-over", "false")}
              onDrop={(e) => {
                e.preventDefault();
                e.currentTarget.setAttribute("data-drag-over", "false");
                if (dragItem) { moveTask(dragItem, lane.id); setDragItem(null); }
              }}
            >
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <h3 className={`text-[14px] font-semibold ${lane.color}`}>{lane.label}</h3>
                  <span className="badge badge-gold">{laneTasks.length}</span>
                </div>
                <button
                  onClick={() => { setShowNewTask(lane.id); setNewTaskTitle(""); }}
                  className="w-8 h-8 rounded-lg flex items-center justify-center hover:bg-white/[0.05] transition-colors"
                  aria-label={`Add task to ${lane.label}`}
                >
                  <Plus className="w-4 h-4 text-text-muted" />
                </button>
              </div>

              {/* New task inline form */}
              {showNewTask === lane.id && (
                <div className="glass-card p-3 mb-3 animate-fade-in">
                  <input
                    autoFocus
                    placeholder="Task title"
                    value={newTaskTitle}
                    onChange={(e) => setNewTaskTitle(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && createTask(lane.id)}
                    className="input-glass mb-2"
                  />
                  <div className="flex items-center gap-2 mb-2">
                    <select value={newTaskPriority} onChange={(e) => setNewTaskPriority(e.target.value)} className="select-glass text-[12px] py-1.5 flex-1">
                      <option value="low">Low</option>
                      <option value="medium">Medium</option>
                      <option value="high">High</option>
                      <option value="urgent">Urgent</option>
                    </select>
                    <input placeholder="Assignee" value={newTaskAssignee} onChange={(e) => setNewTaskAssignee(e.target.value)} className="input-glass text-[12px] py-1.5 flex-1" />
                  </div>
                  <div className="flex items-center gap-2">
                    <button onClick={() => createTask(lane.id)} className="btn-gold text-[12px] py-1.5 px-3 min-h-[36px]">Add</button>
                    <button onClick={() => setShowNewTask(null)} className="btn-ghost text-[12px] py-1.5 px-3 min-h-[36px]">Cancel</button>
                  </div>
                </div>
              )}

              {/* Task cards */}
              <div className="space-y-2">
                {laneTasks.map((task: any) => (
                  <div
                    key={task.id}
                    draggable
                    onDragStart={() => setDragItem(task.id)}
                    onDragEnd={() => setDragItem(null)}
                    className={`glass-card-elevated p-3 cursor-grab active:cursor-grabbing ${PRI_BORDER[task.priority] || "priority-low"}`}
                  >
                    <div className="flex items-start gap-2">
                      <GripVertical className="w-4 h-4 text-text-muted mt-0.5 shrink-0 opacity-40" aria-hidden="true" />
                      <div className="flex-1 min-w-0">
                        <p className="text-[13px] text-text-primary font-medium">{task.title}</p>
                        <div className="flex items-center gap-2 mt-1.5">
                          <span className={`badge ${PRI_CLASS[task.priority] || "badge-blue"}`}>
                            {PRI_LABEL[task.priority] || "Low"}
                          </span>
                          {task.assignee && (
                            <span className="text-[11px] text-text-muted">{task.assignee}</span>
                          )}
                        </div>
                      </div>
                      <button onClick={() => deleteTask(task.id)} className="opacity-0 group-hover:opacity-100 p-1 text-text-muted hover:text-error transition-colors" aria-label="Delete task">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
