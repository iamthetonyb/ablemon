"use client";

import useSWR from "swr";

const fetcher = (url: string) => fetch(url).then((r) => r.json());

const MATURITY_BADGES: Record<string, string> = {
  beta: "badge-orange",
  stable: "badge-green",
};

export default function CollectionsPage() {
  const { data, error, isLoading } = useSWR("/api/collections", fetcher, {
    refreshInterval: 60000,
  });

  if (isLoading) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div className="h-8 w-52 skeleton" />
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((index) => (
            <div key={index} className="h-52 skeleton" />
          ))}
        </div>
      </div>
    );
  }

  if (error || data?.error) {
    return (
      <div className="glass-card p-8 text-center">
        <p className="text-error text-sm mb-2">Failed to load collections</p>
        <p className="text-text-muted text-xs font-mono">
          {data?.error || error?.message}
        </p>
      </div>
    );
  }

  const collections = Array.isArray(data?.collections) ? data.collections : [];

  return (
    <div className="animate-fade-in">
      <div className="mb-8">
        <h2 className="text-2xl font-bold text-text-primary">Collections</h2>
        <p className="text-text-secondary text-[14px] mt-1">
          Curated resource bundles for distillation, offline knowledge, pentesting, and
          research workflows.
        </p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {collections.map((collection: any) => (
          <div key={collection.id} className="glass-card-elevated p-5">
            <div className="flex items-start justify-between gap-4 mb-4">
              <div>
                <h3 className="text-[16px] font-semibold text-white">{collection.name}</h3>
                <p className="text-[12px] text-text-muted font-mono mt-1">{collection.id}</p>
              </div>
              <span
                className={`badge ${
                  MATURITY_BADGES[collection.maturity] || "badge-blue"
                }`}
              >
                {collection.maturity}
              </span>
            </div>

            <p className="text-[13px] text-text-secondary mb-4">{collection.summary}</p>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm text-text-secondary">
              <div>
                <p className="text-text-muted uppercase tracking-wide mb-2">Resources</p>
                <div className="flex gap-2 flex-wrap">
                  {collection.resources?.map((resource: string) => (
                    <span key={resource} className="badge badge-blue">
                      {resource}
                    </span>
                  ))}
                </div>
              </div>
              <div>
                <p className="text-text-muted uppercase tracking-wide mb-2">Modules</p>
                <div className="flex gap-2 flex-wrap">
                  {collection.modules?.map((module: string) => (
                    <span key={module} className="badge badge-cyan">
                      {module}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            {collection.notes?.length > 0 && (
              <div className="mt-4 border-t border-border-subtle pt-4 space-y-2">
                {collection.notes.map((note: string) => (
                  <p key={note} className="text-[12px] text-text-secondary">
                    {note}
                  </p>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
