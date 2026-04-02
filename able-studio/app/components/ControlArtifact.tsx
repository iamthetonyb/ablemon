import type { ReactNode } from "react";

interface ControlArtifactProps {
  artifact?: {
    kind?: string;
    title?: string;
    content?: unknown;
  } | null;
}

export default function ControlArtifact({ artifact }: ControlArtifactProps) {
  if (!artifact) return null;

  const title = artifact.title || "Artifact";
  const kind = artifact.kind || "json";

  let body: ReactNode;
  if (kind === "html" && typeof artifact.content === "string") {
    body = (
      <iframe
        title={title}
        srcDoc={artifact.content}
        sandbox=""
        className="w-full h-[320px] rounded-xl border border-border-subtle bg-surface"
      />
    );
  } else if (kind === "text") {
    body = (
      <pre className="text-xs text-text-secondary whitespace-pre-wrap break-words font-mono">
        {String(artifact.content || "")}
      </pre>
    );
  } else {
    body = (
      <pre className="text-xs text-text-secondary whitespace-pre-wrap break-words font-mono">
        {typeof artifact.content === "string"
          ? artifact.content
          : JSON.stringify(artifact.content ?? {}, null, 2)}
      </pre>
    );
  }

  return (
    <section className="glass-card-elevated p-5">
      <div className="flex items-center justify-between gap-3 mb-4">
        <h3 className="text-sm font-semibold text-white">{title}</h3>
        <span className="badge badge-blue">{kind}</span>
      </div>
      {body}
    </section>
  );
}
