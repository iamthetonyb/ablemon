"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { 
  DollarSign, 
  Briefcase, 
  CheckCircle2, 
  Timer,
  Plus,
  ArrowUpRight,
  MoreVertical
} from "lucide-react";
import { format, differenceInDays } from "date-fns";

const TARGET_DATE = new Date("2028-02-11");
const GOAL_REVENUE = 100000;

export default function Dashboard() {
  const [timeText, setTimeText] = useState("");
  const [greeting, setGreeting] = useState("");

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      setTimeText(format(now, "MMM do, yyyy • h:mm a"));
      const hour = now.getHours();
      if (hour < 12) setGreeting("Good morning");
      else if (hour < 18) setGreeting("Good afternoon");
      else setGreeting("Good evening");
    };
    
    updateTime();
    const interval = setInterval(updateTime, 60000);
    return () => clearInterval(interval);
  }, []);

  const daysToGoal = differenceInDays(TARGET_DATE, new Date());

  const metrics = [
    {
      label: "Net Revenue Progress",
      value: "$12,450",
      target: "/ $100k",
      icon: DollarSign,
      trend: "+14.2%",
      color: "border-t-green-500",
      glow: "shadow-[0_0_15px_rgba(34,197,94,0.15)]"
    },
    {
      label: "Active Projects",
      value: "8",
      icon: Briefcase,
      trend: "2 closing soon",
      color: "border-t-blue-500",
      glow: "shadow-[0_0_15px_rgba(59,130,246,0.15)]"
    },
    {
      label: "Tasks Today",
      value: "12",
      icon: CheckCircle2,
      trend: "5 completed",
      color: "border-t-purple-500",
      glow: "shadow-[0_0_15px_rgba(168,85,247,0.15)]"
    },
    {
      label: "Days to Goal",
      value: daysToGoal.toString(),
      target: " days",
      icon: Timer,
      trend: "Feb 11, 2028",
      color: "border-t-gold",
      glow: "shadow-[0_0_15px_var(--color-gold-glow)]"
    }
  ];

  return (
    <div className="space-y-8 animate-in fade-in duration-700">
      {/* Welcome Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 glass-card p-6 border-l-4 border-l-gold">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-white mb-1">
            {greeting}, <span className="text-gold">Tony B.</span>
          </h1>
          <p className="text-gray-400">KingCRO Mission Control</p>
        </div>
        <div className="text-right">
          <div className="font-mono text-sm text-gold/80 bg-gold/10 px-3 py-1 rounded-full border border-gold/20 inline-block">
            {timeText}
          </div>
        </div>
      </div>

      {/* Metrics Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {metrics.map((metric, i) => (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.1 }}
            key={metric.label}
            className={`glass-card p-5 border-t-2 ${metric.color} ${metric.glow} relative overflow-hidden group`}
          >
            <div className="flex justify-between items-start mb-4">
              <div className="p-2 bg-white/5 rounded-lg border border-white/10 group-hover:bg-white/10 transition-colors">
                <metric.icon className="w-5 h-5 text-gray-300 group-hover:text-white transition-colors" />
              </div>
              <span className="text-xs font-medium text-gray-400 bg-black/20 px-2 py-1 rounded-full border border-glass-border flex items-center gap-1">
                {metric.trend} <ArrowUpRight className="w-3 h-3" />
              </span>
            </div>
            
            <h3 className="text-sm font-medium text-gray-400 mb-1">{metric.label}</h3>
            <div className="flex items-baseline gap-1">
              <span className="text-3xl font-bold tracking-tight text-white">{metric.value}</span>
              {metric.target && <span className="text-sm font-medium text-gray-500">{metric.target}</span>}
            </div>
          </motion.div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Top Priorities */}
        <div className="lg:col-span-2 glass-card p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-xl font-semibold text-white">Top Priorities</h2>
            <button className="p-2 bg-gold/10 text-gold hover:bg-gold/20 hover:scale-105 transition-all rounded-md border border-gold/20 flex items-center gap-2 text-sm font-medium">
              <Plus className="w-4 h-4" /> Add Priority
            </button>
          </div>
          <div className="space-y-3">
            {[
              "Review WorldCup 2026 Ad Variants",
              "Execute AGI Persistence Layer Refactor",
              "Client Onboarding for HoodieFlags",
            ].map((task, i) => (
              <div key={i} className="group flex items-center gap-4 p-4 bg-white/5 border border-glass-border rounded-xl hover:bg-white/10 transition-colors">
                <div className="w-5 h-5 rounded border border-gray-500 flex-shrink-0 group-hover:border-gold transition-colors cursor-pointer" />
                <span className="flex-grow text-gray-200">{task}</span>
                <button className="opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-white transition-all">
                  <MoreVertical className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* Activity Feed */}
        <div className="glass-card p-6 flex flex-col h-full">
          <h2 className="text-xl font-semibold text-white mb-6">Live Activity</h2>
          <div className="relative flex-grow">
            {/* Timeline line */}
            <div className="absolute left-2 top-2 bottom-2 w-[1px] bg-gradient-to-b from-gold/50 via-white/10 to-transparent" />
            
            <div className="space-y-6">
              {[
                { time: "10m ago", text: "ATLAS compiled claude_code_provider.py", type: "system" },
                { time: "1h ago", text: "HoodieFlags copy variants generated", type: "agent" },
                { time: "3h ago", text: "Client portal DB schema initialized", type: "system" },
              ].map((item, i) => (
                <div key={i} className="flex gap-4 relative z-10 pl-8">
                  <div className={`absolute left-0 w-4 h-4 rounded-full border-[3px] border-primary-bg ${
                    item.type === 'system' ? 'bg-gold' : 'bg-blue-500'
                  } shadow-[0_0_10px_currentColor]`} />
                  <div>
                    <p className="text-sm text-gray-300">{item.text}</p>
                    <span className="text-xs text-gray-500 font-mono">{item.time}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
