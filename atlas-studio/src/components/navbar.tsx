"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, LayoutDashboard, KanbanSquare, GitBranch, FileText, Search } from "lucide-react";
import { clsx } from "clsx";

export function Navbar() {
  const pathname = usePathname();

  const navLinks = [
    { name: "Dashboard", href: "/", icon: LayoutDashboard },
    { name: "Projects", href: "/projects", icon: KanbanSquare },
    { name: "Timeline", href: "/timeline", icon: GitBranch }, // fallback icon
    { name: "Tracking", href: "/tracking", icon: FileText },
  ];

  return (
    <nav className="fixed top-0 w-full z-50 glass-card rounded-none border-x-0 border-t-0 px-6 py-3 flex items-center justify-between">
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-full bg-gold/20 border border-gold/50 flex items-center justify-center">
          <Activity className="w-4 h-4 text-gold" />
        </div>
        <span className="font-semibold tracking-wide text-white">ATLAS <span className="text-gold">STUDIO</span></span>
      </div>

      <div className="flex bg-white/5 rounded-lg p-1 border border-glass-border">
        {navLinks.map((link) => {
          const Icon = link.icon;
          const isActive = pathname === link.href;
          return (
            <Link
              key={link.name}
              href={link.href}
              className={clsx(
                "flex items-center gap-2 px-4 py-1.5 text-sm font-medium rounded-md transition-all duration-300",
                isActive 
                  ? "bg-white/10 text-gold shadow-[0_0_10px_var(--color-gold-glow)]" 
                  : "text-gray-400 hover:text-white hover:bg-white/5"
              )}
            >
              <Icon className="w-4 h-4" />
              {link.name}
            </Link>
          );
        })}
      </div>

      <div className="flex items-center gap-4">
        <div className="relative">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input 
            type="text" 
            placeholder="Search missions... (⌘K)" 
            className="bg-black/20 border border-glass-border rounded-full pl-9 pr-4 py-1.5 text-sm text-white focus:outline-none focus:border-gold/50 transition-colors w-64"
          />
        </div>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-green-500/10 border border-green-500/20">
          <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse" />
          <span className="text-xs font-medium text-green-400">Agents Online</span>
        </div>
      </div>
    </nav>
  );
}
