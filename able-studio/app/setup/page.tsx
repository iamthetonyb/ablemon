"use client";

import useSWR from "swr";

import { fetcher } from "@/lib/fetcher";

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

export default function SetupPage() {
  const { data, error, isLoading } = useSWR("/api/setup-wizard", fetcher, {
    refreshInterval: 60000,
    errorRetryCount: 3,
  });

  if (isLoading) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div className="h-8 w-48 skeleton" />
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <div className="xl:col-span-2 h-96 skeleton" />
          <div className="h-96 skeleton" />
        </div>
      </div>
    );
  }

  if (error || data?.error) {
    return (
      <div className="glass-card p-8 text-center">
        <p className="text-error text-sm mb-2">Failed to load setup wizard</p>
        <p className="text-text-muted text-xs font-mono">
          {data?.error || error?.message}
        </p>
      </div>
    );
  }

  const steps = Array.isArray(data?.steps) ? data.steps : [];
  const collections = Array.isArray(data?.collections) ? data.collections : [];

  return (
    <div className="animate-fade-in">
      <div className="mb-8">
        <h2 className="text-2xl font-bold text-text-primary">
          {data?.title || "Setup Wizard"}
        </h2>
        <p className="text-text-secondary text-[14px] mt-1">
          First-run validation for the gateway, control API, Ollama, memory, and optional
          command-center bundles.
        </p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <section className="xl:col-span-2 glass-card-elevated p-6">
          <h3 className="text-sm font-semibold text-white mb-5">Core Checks</h3>
          <div className="space-y-4">
            {steps.map((step: any, index: number) => (
              <div
                key={step.id || index}
                className="relative border border-border-subtle rounded-2xl px-5 py-4"
              >
                <div className="flex items-start gap-4">
                  <div className="w-9 h-9 rounded-full border border-gold-400/20 bg-gold-400/10 text-gold-400 flex items-center justify-center text-sm font-semibold shrink-0">
                    {index + 1}
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-3 flex-wrap">
                      <span
                        className={`status-dot ${
                          STATUS_DOTS[step.status] || "status-dot-inactive"
                        }`}
                      />
                      <h4 className="text-sm font-semibold text-text-primary">{step.label}</h4>
                      <span className="badge badge-blue">{step.status}</span>
                    </div>
                    <p className="text-sm text-text-secondary mt-2">{step.description}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="glass-card-elevated p-6">
          <h3 className="text-sm font-semibold text-white mb-5">Suggested Bundles</h3>
          <div className="space-y-3">
            {collections.map((collection: any) => (
              <div key={collection.id} className="border border-border-subtle rounded-2xl p-4">
                <div className="flex items-center justify-between gap-3">
                  <h4 className="text-sm font-semibold text-text-primary">{collection.name}</h4>
                  <span className="badge badge-gold">{collection.maturity}</span>
                </div>
                <p className="text-xs text-text-secondary mt-2">{collection.summary}</p>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
