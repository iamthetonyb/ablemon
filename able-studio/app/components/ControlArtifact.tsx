type Artifact = {
  title?: string;
  label?: string;
  kind?: string;
  description?: string;
  content?: string;
  text?: string;
  html?: string;
  json?: unknown;
  lines?: string[];
};

function artifactBody(artifact?: Artifact): { mode: "empty" | "html" | "text"; value: string } {
  if (!artifact) {
    return { mode: "empty", value: "No artifact available." };
  }
  if (artifact.html) {
    return { mode: "html", value: artifact.html };
  }
  if (artifact.content) {
    return { mode: "text", value: artifact.content };
  }
  if (artifact.text) {
    return { mode: "text", value: artifact.text };
  }
  if (Array.isArray(artifact.lines) && artifact.lines.length > 0) {
    return { mode: "text", value: artifact.lines.join("\n") };
  }
  if (artifact.json !== undefined) {
    return { mode: "text", value: JSON.stringify(artifact.json, null, 2) };
  }
  return { mode: "empty", value: artifact.description || "No artifact payload exposed for this resource." };
}

export default function ControlArtifact({ artifact }: { artifact?: Artifact | null }) {
  const title = artifact?.title || artifact?.label || artifact?.kind || "Artifact";
  const detail = artifact?.description || artifact?.kind || "Control plane artifact";
  const body = artifactBody(artifact || undefined);

  return (
    <section className="glass-card-elevated p-5 min-h-[280px]">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <h3 className="text-sm font-semibold text-white">{title}</h3>
          <p className="text-xs text-text-muted mt-1">{detail}</p>
        </div>
        {artifact?.kind && <span className="badge badge-blue">{artifact.kind}</span>}
      </div>

      {body.mode === "html" ? (
        <div
          className="rounded-xl border border-border-subtle bg-black/20 p-4 text-sm text-text-secondary [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-border-subtle [&_td]:p-2 [&_th]:border [&_th]:border-border-subtle [&_th]:p-2"
          dangerouslySetInnerHTML={{ __html: body.value }}
        />
      ) : (
        <pre className="rounded-xl border border-border-subtle bg-black/20 p-4 text-xs text-text-secondary overflow-x-auto whitespace-pre-wrap break-words">
          {body.value}
        </pre>
      )}
    </section>
  );
}
