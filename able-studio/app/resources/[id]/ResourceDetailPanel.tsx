"use client";

import { useState } from "react";
import useSWR from "swr";
import ControlArtifact from "@/app/components/ControlArtifact";

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

export default function ResourceDetailPanel({ resourceId }: { resourceId: string }) {
  const encodedId = encodeURIComponent(resourceId);
  const { data, error, isLoading, mutate } = useSWR(`/api/resources/${encodedId}`, fetcher, {
    refreshInterval: 30000,
    errorRetryCount: 3,
  });
  const [approvedBy, setApprovedBy] = useState("");
  const [runningAction, setRunningAction] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  async function runAction(action: string) {
    setRunningAction(action);
    setActionMessage(null);

    try {
      const response = await fetch(`/api/resources/${encodedId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action,
          approved_by: approvedBy || undefined,
        }),
      });
      const payload = await response.json();
      setActionMessage(
        payload.message ||
          payload.status ||
          `${action} finished with status ${response.status}`,
      );
      await mutate();
    } catch (requestError) {
      setActionMessage(
        requestError instanceof Error
          ? requestError.message
          : `Failed to execute ${action}`,
      );
    } finally {
      setRunningAction(null);
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-6 animate-fade-in">
        <div className="h-8 w-56 skeleton" />
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <div className="xl:col-span-2 h-80 skeleton" />
          <div className="h-80 skeleton" />
        </div>
      </div>
    );
  }

  if (error || data?.error) {
    return (
      <div className="glass-card p-8 text-center">
        <p className="text-error text-sm mb-2">Failed to load resource detail</p>
        <p className="text-text-muted text-xs font-mono">
          {data?.error || error?.message}
        </p>
      </div>
    );
  }

  const artifacts = Array.isArray(data?.artifacts) ? data.artifacts : [];
  const approvalHistory = Array.isArray(data?.approval_history) ? data.approval_history : [];

  return (
    <div className="animate-fade-in">
      <div className="flex items-start justify-between gap-6 mb-8">
        <div>
          <div className="flex items-center gap-3 flex-wrap">
            <span
              className={`status-dot ${
                STATUS_DOTS[data.status] || "status-dot-inactive"
              }`}
            />
            <h2 className="text-2xl font-bold text-text-primary">{data.name}</h2>
            <span className="badge badge-blue">{data.kind}</span>
            <span className="badge badge-gold">{data.control_mode}</span>
          </div>
          <p className="text-text-muted text-[12px] font-mono mt-2">{data.id}</p>
          <p className="text-text-secondary text-[14px] mt-3 max-w-3xl">{data.summary}</p>
        </div>
        <div className="glass-card-elevated px-4 py-3 min-w-[220px]">
          <p className="text-[11px] uppercase tracking-[0.2em] text-text-muted mb-2">
            Status
          </p>
          <p className="text-xl font-semibold text-gold-400">{data.status}</p>
          {data.endpoint && (
            <p className="text-xs text-text-secondary mt-2 break-all">{data.endpoint}</p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
        <section className="xl:col-span-2 glass-card-elevated p-5">
          <h3 className="text-sm font-semibold text-white mb-4">Resource Detail</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm text-text-secondary">
            <div>
              <p className="text-text-muted uppercase tracking-wide mb-1">Dependencies</p>
              <p>{data.dependencies?.length ? data.dependencies.join(", ") : "None"}</p>
            </div>
            <div>
              <p className="text-text-muted uppercase tracking-wide mb-1">Ports</p>
              <p>{data.ports?.length ? data.ports.join(", ") : "n/a"}</p>
            </div>
            <div>
              <p className="text-text-muted uppercase tracking-wide mb-1">Storage Paths</p>
              <p>
                {data.storage_paths?.length ? data.storage_paths.join(", ") : "n/a"}
              </p>
            </div>
            <div>
              <p className="text-text-muted uppercase tracking-wide mb-1">Owner</p>
              <p>{data.owner || "able"}</p>
            </div>
            <div className="md:col-span-2">
              <p className="text-text-muted uppercase tracking-wide mb-1">Allowed Actions</p>
              <div className="flex gap-2 flex-wrap">
                {data.allowed_actions?.length ? (
                  data.allowed_actions.map((action: string) => (
                    <span key={action} className="badge badge-cyan">
                      {action}
                    </span>
                  ))
                ) : (
                  <span className="text-text-secondary">None</span>
                )}
              </div>
            </div>
          </div>
        </section>

        <section className="glass-card-elevated p-5">
          <h3 className="text-sm font-semibold text-white mb-4">Lifecycle Control</h3>
          <p className="text-sm text-text-secondary mb-4">
            Mutating actions require explicit operator identity. The backend refuses
            requests with no approval metadata and records every action to the audit trail.
          </p>
          <input
            className="input-glass mb-4"
            placeholder="approved_by (operator ID or handle)"
            value={approvedBy}
            onChange={(event) => setApprovedBy(event.target.value)}
          />
          <div className="grid grid-cols-2 gap-2">
            {data.allowed_actions?.map((action: string) => (
              <button
                key={action}
                className="btn-ghost"
                onClick={() => runAction(action)}
                disabled={runningAction === action}
              >
                {runningAction === action ? "Running..." : action}
              </button>
            ))}
          </div>
          {actionMessage && (
            <div className="mt-4 text-xs text-text-secondary border border-border-subtle rounded-xl px-3 py-2">
              {actionMessage}
            </div>
          )}
        </section>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 mb-6">
        <ControlArtifact artifact={data.log_artifact} />
        {artifacts.length > 0 ? (
          <ControlArtifact artifact={artifacts[0]} />
        ) : (
          <section className="glass-card-elevated p-5">
            <h3 className="text-sm font-semibold text-white mb-4">Artifacts</h3>
            <p className="text-sm text-text-secondary">
              No additional artifacts exposed for this resource yet.
            </p>
          </section>
        )}
      </div>

      <section className="glass-card-elevated p-5">
        <h3 className="text-sm font-semibold text-white mb-4">Approval History</h3>
        {approvalHistory.length === 0 ? (
          <p className="text-sm text-text-secondary">
            No lifecycle actions recorded for this resource yet.
          </p>
        ) : (
          <div className="space-y-3">
            {approvalHistory.map((entry: any, index: number) => (
              <div
                key={`${entry.timestamp || index}-${entry.action || "action"}`}
                className="border border-border-subtle rounded-xl px-4 py-3"
              >
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="text-sm text-white">{entry.action}</p>
                    <p className="text-xs text-text-muted mt-1">
                      {entry.approved_by || "unknown operator"} · {entry.timestamp}
                    </p>
                  </div>
                  <span className="badge badge-blue">
                    exit {entry.exit_code ?? "n/a"}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
