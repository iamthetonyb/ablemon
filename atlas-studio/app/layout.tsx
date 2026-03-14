import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ATLAS Studio",
  description: "Autonomous Task & Learning Agent System — Control Plane",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen antialiased">
        <div className="flex min-h-screen">
          {/* Sidebar */}
          <nav className="w-64 border-r border-border-subtle bg-surface-elevated/50 backdrop-blur-xl p-6 flex flex-col gap-2">
            <div className="mb-8">
              <h1 className="text-xl font-bold text-gold-400 tracking-tight">ATLAS</h1>
              <p className="text-xs text-white/40 mt-1">Studio Control Plane</p>
            </div>

            <NavLink href="/" label="Dashboard" />
            <NavLink href="/settings" label="Settings" />
            <NavLink href="/audit" label="Audit Logs" />
            <NavLink href="/clients" label="Clients" />

            <div className="mt-auto pt-6 border-t border-border-subtle">
              <p className="text-xs text-white/30">ATLAS v2 — Swarm AGI</p>
            </div>
          </nav>

          {/* Main content */}
          <main className="flex-1 p-8 overflow-auto">{children}</main>
        </div>
      </body>
    </html>
  );
}

function NavLink({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      className="block px-4 py-2.5 rounded-lg text-sm text-white/60 hover:text-white hover:bg-white/5 transition-colors"
    >
      {label}
    </a>
  );
}
