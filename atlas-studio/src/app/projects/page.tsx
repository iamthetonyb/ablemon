"use client";

import { useState } from "react";
import useSWR, { mutate } from "swr";
import { Plus, MoreHorizontal, GripVertical } from "lucide-react";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const LANES = [
  { id: "backlog", label: "Backlog", color: "text-text-muted" },
  { id: "in_progress", label: "In Progress", color: "text-gold-400" },
  { id: "done", label: "Done", color: "text-success" },
];

const PRI_LABEL = ["Low", "Medium", "High", "Urgent"];
const PRI_CLASS = ["badge-blue", "badge-gold", "badge-orange", "badge-red"];

export default function ProjectsPage() {
  const { data: projectData } = useSWR("/api/projects", fetcher);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const { data: taskData } = useSWR(
    selectedProject ? `/api/tasks?project_id=${selectedProject}` : null,
    fetcher
  );
  const [showNewProject, setShowNewProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [showNewTask, setShowNewTask] = useState<string | null>(null);
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [newTaskPriority, setNewTaskPriority] = useState(0);
  const [dragItem, setDragItem] = useState<string | null>(null);

  const projects = projectData?.projects || [];
  const tasks = taskData?.tasks || [];

  async function createProject() {
    if (!newProjectName.trim()) return;
    const optimistic = { id: "temp", name: newProjectName, status: "active", color: "#D4AF37", createdAt: new Date().toISOString() };
    mutate("/api/projects", { projects: [...projects, optimistic] }, false);
    setNewProjectName("");
    setShowNewProject(false);
    await fetch("/api/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: newProjectName }) });
    mutate("/api/projects");
  }

  async function createTask(lane: string) {
    if (!newTaskTitle.trim() || !selectedProject) return;
    const optimistic = { id: "temp-" + Date.now(), title: newTaskTitle, lane, priority: newTaskPriority, projectId: selectedProject, sortOrder: 0, tags: [] };
    mutate(`/api/tasks?project_id=${selectedProject}`, { tasks: [...tasks, optimistic] }, false);
    setNewTaskTitle("");
    setShowNewTask(null);
    setNewTaskPriority(0);
    await fetch("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: newTaskTitle, project_id: selectedProject, lane, priority: newTaskPriority }) });
    mutate(`/api/tasks?project_id=${selectedProject}`);
  }

  async function moveTask(taskId: string, newLane: string) {
    const updated = tasks.map((t: any) => t.id === taskId ? { ...t, lane: newLane } : t);
    mutate(`/api/tasks?project_id=${selectedProject}`, { tasks: updated }, false);
    await fetch("/api/tasks", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: taskId, lane: newLane }) });
    mutate(`/api/tasks?project_id=${selectedProject}`);
  }

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-2xl font-bold text-text-primary">Projects</h2>
          <p className="text-text-secondary text-[14px] mt-1">Kanban task management with drag-and-drop lanes</p>
        </div>
        <button onClick={() => setShowNewProject(true)} className="btn-gold" aria-label="Create new project">
          <Plus className="w-4 h-4" aria-hidden="true" /> New Project
        </button>
      </div>

      {/* New project form */}
      {showNewProject && (
        <div className="glass-card gold-glow p-5 mb-6 animate-fade-in">
          <div className="flex gap-3">
            <input
              autoFocus
              placeholder="Project name"
              value={newProjectName}
              onChange={(e) => setNewProjectName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && createProject()}
              className="input-glass flex-1"
            />
            <button onClick={createProject} className="btn-gold">Create</button>
            <button onClick={() => setShowNewProject(false)} className="btn-ghost">Cancel</button>
          </div>
        </div>
      )}

      {/* Project selector */}
      <div className="flex gap-2 mb-6 flex-wrap">
        {projects.map((p: any) => (
          <button
            key={p.id}
            onClick={() => setSelectedProject(p.id)}
            className={`px-4 py-2.5 rounded-lg text-[13px] font-medium transition-all duration-200 min-h-[44px] ${
              selectedProject === p.id
                ? "bg-gold-400/10 text-gold-400 border border-gold-400/20"
                : "text-text-secondary hover:text-text-primary bg-white/[0.03] border border-border-subtle"
            }`}
          >
            <span className="inline-block w-2.5 h-2.5 rounded-full mr-2" style={{ background: p.color }} />
            {p.name}
          </button>
        ))}
        {projects.length === 0 && !showNewProject && (
          <p className="text-text-muted text-[13px]">No projects yet. Create one to start managing tasks.</p>
        )}
      </div>

      {/* Kanban board */}
      {selectedProject && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {LANES.map((lane) => {
            const laneTasks = tasks.filter((t: any) => t.lane === lane.id);
            return (
              <div
                key={lane.id}
                className="kanban-lane p-4"
                data-drag-over={dragItem ? "false" : undefined}
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
                    <div className="flex items-center gap-2">
                      <select
                        value={newTaskPriority}
                        onChange={(e) => setNewTaskPriority(Number(e.target.value))}
                        className="select-glass text-[12px] py-1.5"
                      >
                        <option value={0}>Low</option>
                        <option value={1}>Medium</option>
                        <option value={2}>High</option>
                        <option value={3}>Urgent</option>
                      </select>
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
                      className={`glass-card-elevated p-3 cursor-grab active:cursor-grabbing ${
                        task.priority >= 3 ? "priority-urgent" : task.priority >= 2 ? "priority-high" : task.priority >= 1 ? "priority-medium" : "priority-low"
                      }`}
                    >
                      <div className="flex items-start gap-2">
                        <GripVertical className="w-4 h-4 text-text-muted mt-0.5 shrink-0" aria-hidden="true" />
                        <div className="flex-1 min-w-0">
                          <p className="text-[13px] text-text-primary font-medium">{task.title}</p>
                          <div className="flex items-center gap-2 mt-1.5">
                            <span className={`badge ${PRI_CLASS[task.priority] || "badge-blue"}`}>
                              {PRI_LABEL[task.priority] || "Low"}
                            </span>
                            {task.dueDate && (
                              <span className="text-[11px] text-text-muted">
                                {new Date(task.dueDate).toLocaleDateString()}
                              </span>
                            )}
                          </div>
                        </div>
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
