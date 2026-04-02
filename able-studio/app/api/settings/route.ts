import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { featureFlags } from "@/drizzle/schema";
import { eq, isNull, and, or } from "drizzle-orm";
import { getToolCatalog } from "@/lib/control-plane";

/**
 * GET /api/settings?org_id=<optional>
 *
 * Returns all feature flags (tool toggles).
 * Used by both the UI and the Python gateway to check which tools are authorized.
 *
 * Python gateway calls this before each agent loop iteration to verify tool access.
 */
export async function GET(req: NextRequest) {
  const orgId = req.nextUrl.searchParams.get("org_id");
  let catalog: Array<Record<string, unknown>> = [];

  try {
    const payload = await getToolCatalog(orgId || undefined);
    catalog = payload.catalog;
  } catch {
    catalog = [];
  }

  const flags = await db
    .select()
    .from(featureFlags)
    .where(
      orgId
        ? or(
            eq(featureFlags.organizationId, orgId),
            isNull(featureFlags.organizationId),
          )
        : isNull(featureFlags.organizationId)
    );

  const flagMap: Record<string, (typeof flags)[number]> = {};
  for (const flag of flags) {
    const existing = flagMap[flag.toolName];
    if (!existing || existing.organizationId === null) {
      flagMap[flag.toolName] = flag;
    }
  }

  const toolMap: Record<string, { enabled: boolean; requires_approval: boolean; risk_level: string }> = {};
  const catalogRows = catalog.map((tool) => {
    const name = String(tool.name);
    const override = flagMap[name];
    const enabled = override?.enabled ?? Boolean(tool.enabled ?? tool.enabled_by_default ?? true);
    const requiresApproval =
      override?.requiresApproval ?? Boolean(tool.requires_approval ?? false);
    const riskLevel = String(override?.riskLevel || tool.risk_level || "medium");

    toolMap[name] = {
      enabled,
      requires_approval: requiresApproval,
      risk_level: riskLevel,
    };

    return {
      name,
      toolName: name,
      displayName: String(tool.display_name || name),
      description: typeof tool.description === "string" ? tool.description : null,
      category: String(tool.category || "system"),
      enabled,
      requiresApproval,
      riskLevel,
      readOnly: Boolean(tool.read_only ?? true),
      concurrentSafe: Boolean(tool.concurrent_safe ?? true),
      surface: String(tool.surface || "system"),
      artifactKind: String(tool.artifact_kind || "markdown"),
      enabledByDefault: Boolean(tool.enabled_by_default ?? true),
      tags: Array.isArray(tool.tags) ? tool.tags : [],
    };
  });

  if (catalogRows.length === 0) {
    for (const flag of Object.values(flagMap)) {
      toolMap[flag.toolName] = {
        enabled: flag.enabled,
        requires_approval: flag.requiresApproval,
        risk_level: flag.riskLevel || "medium",
      };
    }
  }

  return NextResponse.json({
    organization_id: orgId || "global",
    tools: toolMap,
    catalog: catalogRows,
    timestamp: new Date().toISOString(),
  });
}

/**
 * PUT /api/settings
 *
 * Update a feature flag toggle.
 * Body: { tool_name, enabled, requires_approval?, org_id? }
 */
export async function PUT(req: NextRequest) {
  const body = await req.json();
  const { tool_name, enabled, requires_approval, org_id } = body;

  if (!tool_name || typeof enabled !== "boolean") {
    return NextResponse.json({ error: "tool_name and enabled required" }, { status: 400 });
  }

  // Update existing flag
  const condition = org_id
    ? and(eq(featureFlags.toolName, tool_name), eq(featureFlags.organizationId, org_id))
    : and(eq(featureFlags.toolName, tool_name), isNull(featureFlags.organizationId));

  const updated = await db
    .update(featureFlags)
    .set({
      enabled,
      ...(requires_approval !== undefined && { requiresApproval: requires_approval }),
      updatedAt: new Date(),
    })
    .where(condition)
    .returning();

  if (updated.length > 0) {
    return NextResponse.json({ success: true, flag: updated[0] });
  }

  let catalogEntry: Record<string, unknown> | undefined;
  try {
    const payload = await getToolCatalog(org_id || undefined);
    catalogEntry = payload.catalog.find((tool) => tool.name === tool_name);
  } catch {
    catalogEntry = undefined;
  }

  const displayName = String(catalogEntry?.display_name || tool_name);
  const description =
    typeof catalogEntry?.description === "string" ? String(catalogEntry.description) : null;
  const category = String(catalogEntry?.category || "system");
  const riskLevel = String(catalogEntry?.risk_level || "medium");
  const requiresApproval =
    requires_approval !== undefined
      ? requires_approval
      : Boolean(catalogEntry?.requires_approval ?? false);

  const inserted = await db
    .insert(featureFlags)
    .values({
      organizationId: org_id || null,
      toolName: tool_name,
      displayName,
      description,
      category,
      enabled,
      requiresApproval,
      riskLevel,
      updatedAt: new Date(),
      updatedBy: null,
    })
    .returning();

  return NextResponse.json({ success: true, flag: inserted[0] });
}
