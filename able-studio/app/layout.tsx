"use client";

import "./globals.css";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useEffect } from "react";
import ChatBubble from "./components/ChatBubble";
import {
  LayoutDashboard,
  Kanban,
  CalendarRange,
  Brain,
  Settings,
  ScrollText,
  Users,
  Contact,
  Zap,
  Server,
  Boxes,
  Wrench,
} from "lucide-react";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/projects", label: "Projects", icon: Kanban },
  { href: "/timeline", label: "Timeline", icon: CalendarRange },
  { href: "/tracking", label: "Tracking", icon: Brain },
  { href: "/crm", label: "CRM", icon: Contact },
  { href: "/clients", label: "Clients", icon: Users },
  { href: "/audit", label: "Audit Logs", icon: ScrollText },
  { href: "/resources", label: "Resources", icon: Server },
  { href: "/collections", label: "Collections", icon: Boxes },
  { href: "/setup", label: "Setup", icon: Wrench },
  { href: "/settings", label: "Settings", icon: Settings },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className="bg-surface min-h-screen flex antialiased" suppressHydrationWarning>
        {/* Sidebar */}
        <nav
          className="w-[260px] shrink-0 border-r border-border-subtle bg-surface-elevated/50 backdrop-blur-sm flex flex-col"
          aria-label="Main navigation"
        >
          {/* Brand */}
          <div className="p-6 pb-4 border-b border-border-subtle">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-gold-400 to-gold-600 flex items-center justify-center">
                {mounted && <Zap className="w-5 h-5 text-surface" aria-hidden="true" />}
              </div>
              <div>
                <h1 className="text-[15px] font-bold text-text-primary tracking-tight">ABLE</h1>
                <p className="text-[11px] text-text-muted font-medium tracking-wide uppercase">
                  Studio Control Plane
                </p>
              </div>
            </div>
            <div className="mt-4 flex items-center gap-2">
              <span className="status-dot status-dot-active" aria-label="System online" />
              <span className="text-[12px] text-text-secondary">System Online</span>
            </div>
          </div>

          {/* Nav links */}
          <div className="flex-1 py-3 px-3 space-y-1 overflow-y-auto">
            {NAV_ITEMS.map((item) => {
              const active =
                pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));
              const Icon = item.icon;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-[14px] font-medium transition-all duration-200 min-h-[44px] ${
                    active
                      ? "bg-gold-400/10 text-gold-400 border border-gold-400/20"
                      : "text-text-secondary hover:text-text-primary hover:bg-white/[0.03] border border-transparent"
                  }`}
                  aria-current={active ? "page" : undefined}
                >
                  {mounted && <Icon className="w-[18px] h-[18px] shrink-0" aria-hidden="true" />}
                  {!mounted && <span className="w-[18px] h-[18px] shrink-0" />}
                  {item.label}
                </Link>
              );
            })}
          </div>

          {/* Footer */}
          <div className="p-4 border-t border-border-subtle">
            <div className="text-[11px] text-text-muted">
              <p>ABLE Studio v0.3.0</p>
              <p className="mt-0.5">Registry + resource plane</p>
            </div>
          </div>
        </nav>

        {/* Main content */}
        <main className="flex-1 min-h-screen overflow-y-auto">
          <div className="max-w-[1400px] mx-auto p-8">{children}</div>
        </main>

        <ChatBubble />
      </body>
    </html>
  );
}
