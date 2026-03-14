export default function DashboardPage() {
  return (
    <div>
      <h2 className="text-2xl font-semibold text-white mb-2">Dashboard</h2>
      <p className="text-white/40 mb-8">ATLAS Swarm Control Plane</p>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <MetricCard title="Active Tools" value="8" subtitle="MCP Skills" />
        <MetricCard title="Audit Events" value="—" subtitle="Last 24h" />
        <MetricCard title="Organizations" value="1" subtitle="Tenants" />
      </div>
    </div>
  );
}

function MetricCard({ title, value, subtitle }: { title: string; value: string; subtitle: string }) {
  return (
    <div className="glass-card gold-glow p-6">
      <p className="text-xs text-white/40 uppercase tracking-wider mb-2">{title}</p>
      <p className="text-3xl font-bold text-gold-400">{value}</p>
      <p className="text-sm text-white/50 mt-1">{subtitle}</p>
    </div>
  );
}
