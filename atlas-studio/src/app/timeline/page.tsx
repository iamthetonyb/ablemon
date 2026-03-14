"use client";

import { motion } from "framer-motion";
import { CheckCircle2, Circle, Calendar, Rocket, Target, Zap } from "lucide-react";
import { clsx } from "clsx";

interface Milestone {
  text: string;
  completed: boolean;
}

interface Phase {
  id: string;
  title: string;
  dateRange: string;
  description: string;
  isActive: boolean;
  icon: any;
  milestones: Milestone[];
}

const ROADMAP: Phase[] = [
  {
    id: "phase-1",
    title: "Phase 1: Mission Control Foundation",
    dateRange: "March 2026",
    description: "Initialize the command center, set up database architectures, and build the premium glassmorphic UI.",
    isActive: false,
    icon: Rocket,
    milestones: [
      { text: "Scaffold Next.js 16 (Turbopack) & Tailwind CSS", completed: true },
      { text: "Design System (Dark Glassmorphism & Gold)", completed: true },
      { text: "Build 4-Tab Layout (Dashboard, Projects, Timeline, Tracking)", completed: true },
      { text: "Configure Neon Serverless Postgres DB & Drizzle ORM", completed: false },
    ]
  },
  {
    id: "phase-2",
    title: "Phase 2: Swarm Telemetry Hooks",
    dateRange: "April 2026",
    description: "Wire ATLAS Python orchestrator directly into the Next.js API to emit live agent execution logs and research contexts.",
    isActive: true,
    icon: Zap,
    milestones: [
      { text: "Expose secure Next.js API `/api/swarm/logs`", completed: false },
      { text: "Modify ATLAS `gateway.py` to pipe runtime data", completed: false },
      { text: "Build OpenAI-SDK style active log terminal UI", completed: false },
      { text: "Implement Agent Billing / Token usage calculations", completed: false },
    ]
  },
  {
    id: "phase-3",
    title: "Phase 3: AGI Client Portals",
    dateRange: "May - July 2026",
    description: "Launch isolated dashboards for clients to monitor their specific agent chains and output generation securely.",
    isActive: false,
    icon: Target,
    milestones: [
      { text: "Multi-tenant logic with NextAuth v5 roles", completed: false },
      { text: "Client-specific Dashboard rendering", completed: false },
      { text: "Telegram Channel Webhook Integration for Client Output", completed: false },
      { text: "Scale down to $0 when idle via Serverless architecture", completed: false },
    ]
  }
];

export default function Timeline() {
  return (
    <div className="max-w-4xl mx-auto animate-in fade-in duration-700">
      <div className="mb-12 text-center md:text-left">
        <h1 className="text-3xl font-bold tracking-tight text-white mb-2">AGI Roadmap</h1>
        <p className="text-gray-400 max-w-2xl">Strategic timeline to reach multi-client scale and $100k/m MRR by February 2028.</p>
      </div>

      <div className="relative border-l-2 border-glass-border ml-4 md:ml-8 space-y-12 pb-12">
        {ROADMAP.map((phase, i) => (
          <motion.div 
            key={phase.id}
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: i * 0.2 }}
            className="relative pl-8 md:pl-12"
          >
            {/* Timeline Node */}
            <div className={clsx(
              "absolute -left-[17px] md:-left-[21px] top-0 w-8 h-8 md:w-10 md:h-10 rounded-full flex items-center justify-center border-4 border-primary-bg transition-colors duration-500",
              phase.isActive ? "bg-gold text-black shadow-[0_0_20px_var(--color-gold-glow)]" : "bg-black/50 border-glass-border text-gray-500"
            )}>
              <phase.icon className="w-4 h-4 md:w-5 md:h-5" />
            </div>

            {/* Phase Card */}
            <div className={clsx(
              "glass-card p-6 md:p-8 transition-all duration-500 relative overflow-hidden",
              phase.isActive ? "border-gold/50 shadow-[0_0_30px_rgba(212,175,55,0.1)]" : "hover:border-white/20 hover:bg-white/5"
            )}>
              {phase.isActive && (
                <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-gold/0 via-gold to-gold/0 opacity-50" />
              )}
              
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-4">
                <h2 className={clsx("text-xl md:text-2xl font-bold", phase.isActive ? "text-white" : "text-gray-300")}>
                  {phase.title}
                </h2>
                <div className="flex items-center gap-2 text-sm font-medium bg-black/30 px-3 py-1.5 rounded-full border border-glass-border w-fit text-gray-400">
                  <Calendar className="w-4 h-4 text-gold/70" />
                  {phase.dateRange}
                </div>
              </div>

              <p className="text-gray-400 mb-8 leading-relaxed">
                {phase.description}
              </p>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {phase.milestones.map((milestone, idx) => (
                  <div key={idx} className="flex items-start gap-3">
                    {milestone.completed ? (
                      <CheckCircle2 className="w-5 h-5 text-gold flex-shrink-0 mt-0.5 shadow-[0_0_10px_var(--color-gold-glow)] rounded-full" />
                    ) : (
                      <Circle className="w-5 h-5 text-gray-600 flex-shrink-0 mt-0.5" />
                    )}
                    <span className={clsx(
                      "text-sm font-medium",
                      milestone.completed ? "text-gray-200" : "text-gray-500"
                    )}>
                      {milestone.text}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}
