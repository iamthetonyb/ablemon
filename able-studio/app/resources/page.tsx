"use client";

import Link from "next/link";
import useSWR from "swr";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const STATUS_DOTS: Record<string, string> = {
  active: "status-dot-active",
  healthy: "status-dot-active",
  available: "status-dot-active",
  running: "status-dot-active",
  offline: "status-dot-error",
  failed: "status-dot-error",
  missing: "status-dot-error",
  planned: "status-dot-warning",
  unknown: "status-dot-inactive",
};

export default function ResourcesPage() {
  const { data, error, isLoading } = useSWR("/api/resources", fetcher, {
    refreshInterval: 30000,
    errorRetryCount: 3,
  });

  if (isLoading) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div className="h-8 w-48 skeleton" />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((index) => (
            <div key={index} className="h-48 skeleton" />
          ))}
        </div>
      </div>
    );
  }

  if (error || data?.error) {
    return (
      <div className="glass-card p-8 text-center">
        <p className="text-error text-sm mb-2">Failed to load resources</p>
        <p className="text-text-muted text-xs font-mono">
          {data?.error || error?.message}
        </p>
      </div>
    );
  }

  const resources = Array.isArray(data?.resources) ? data.resources : [];

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between gap-4 mb-8">
        <div>
          <h2 className="text-2xl font-bold text-text-primary">Resources</h2>
          <p className="text-text-secondary text-[14px] mt-1">
            Runtime inventory for services, models, storage, and optional local modules.
          </p>
        </div>
        <div className="glass-card-elevated px-4 py-3">
          <p className="text-[11px] uppercase tracking-[0.2em] text-text-muted mb-1">
            Inventory
          </p>
          <p className="text-xl font-semibold text-gold-400">{resources.length}</p>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {resources.map((resource: any) => (
          <Link
            key={resource.id}
            href={`/resources/${encodeURIComponent(resource.id)}`}
            className="glass-card-elevated p-5 block hover:border-gold-400/30 transition-colors"
          >
            <div className="flex items-start justify-between gap-4 mb-4">
              <div>
                <div className="flex items-center gap-3 flex-wrap">
                  <span
                    className={`status-dot ${
                      STATUS_DOTS[resource.status] || "status-dot-inactive"
                    }`}
                  />
                  <h3 className="text-[15px] font-semibold text-text-primary">
                    {resource.name}
                  </h3>
                  <span className="badge badge-blue">{resource.kind}</span>
                  <span className="badge badge-gold">{resource.control_mode}</span>
                </div>
                <p className="text-[12px] text-text-muted font-mono mt-1">{resource.id}</p>
              </div>
              <span className="text-xs text-text-secondary">{resource.status}</span>
            </div>

            <p className="text-[13px] text-text-secondary mb-4">{resource.summary}</p>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-[12px] text-text-secondary">
              <div>
                <p className="text-text-muted uppercase tracking-wide mb-1">Dependencies</p>
                <p>{resource.dependencies?.length ? resource.dependencies.join(", ") : "None"}</p>
              </div>
              <div>
                <p className="text-text-muted uppercase tracking-wide mb-1">Ports</p>
                <p>{resource.ports?.length ? resource.ports.join(", ") : "n/a"}</p>
              </div>
              <div>
                <p className="text-text-muted uppercase tracking-wide mb-1">Storage</p>
                <p>
                  {resource.storage_paths?.length
                    ? resource.storage_paths.slice(0, 2).join(", ")
                    : "n/a"}
                </p>
              </div>
              <div>
                <p className="text-text-muted uppercase tracking-wide mb-1">Last Action</p>
                <p>{resource.last_action?.action || "None recorded"}</p>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
