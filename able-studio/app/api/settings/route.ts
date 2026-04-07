import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { featureFlags } from "@/drizzle/schema";
import { eq, and, isNull } from "drizzle-orm";
import { getToolCatalog } from "@/lib/control-plane";

// GET /api/settings — used by the settings page AND by the gateway (fetch_tool_settings)
export async function GET(req: NextRequest) {
  const { searchParams } = req.nextUrl;
  const orgId = searchParams.get("org_id") ?? undefined;

  // Load DB overrides (null org_id = global defaults)
  const dbFlags = await db
    .select({
      toolName: featureFlags.toolName,
      enabled: featureFlags.enabled,
      requiresApproval: featureFlags.requiresApproval,
      riskLevel: featureFlags.riskLevel,
    })
    .from(featureFlags)
    .where(
      orgId
        ? eq(featureFlags.organizationId, orgId)
        : isNull(featureFlags.organizationId)
    );

  // Build tools map for gateway consumption: { [toolName]: { enabled, requiresApproval } }
  const toolsMap: Record<string, { enabled: boolean; requiresApproval: boolean }> = {};
  for (const flag of dbFlags) {
    toolsMap[flag.toolName] = {
      enabled: flag.enabled,
      requiresApproval: flag.requiresApproval,
    };
  }

  // Try to get live catalog from gateway (for full display with descriptions etc.)
  let catalog: Record<string, unknown>[] = [];
  try {
    const gatewayData = await getToolCatalog(orgId);
    catalog = (gatewayData.catalog ?? []) as Record<string, unknown>[];

    // Merge DB overrides into catalog entries
    catalog = catalog.map((entry) => {
      const toolName = entry.toolName as string || entry.name as string;
      const override = toolsMap[toolName];
      if (override) {
        return { ...entry, ...override };
      }
      return entry;
    });
  } catch {
    // Gateway not running — return DB flags as minimal catalog
    catalog = dbFlags.map((f) => ({
      name: f.toolName,
      toolName: f.toolName,
      displayName: f.toolName,
      description: null,
      category: "system",
      enabled: f.enabled,
      requiresApproval: f.requiresApproval,
      riskLevel: f.riskLevel ?? "medium",
      readOnly: false,
      concurrentSafe: true,
      surface: "all",
      artifactKind: "none",
      enabledByDefault: true,
      tags: [],
    }));
  }

  return NextResponse.json({ catalog, tools: toolsMap });
}

// PUT /api/settings — toggle tool enabled/requiresApproval
export async function PUT(req: NextRequest) {
  try {
    const body = await req.json();
    const { tool_name, enabled, requires_approval, org_id } = body;

    if (!tool_name) {
      return NextResponse.json({ error: "tool_name required" }, { status: 400 });
    }

    const orgIdValue = org_id ?? null;

    // Check if exists
    const condition = orgIdValue
      ? and(eq(featureFlags.organizationId, orgIdValue), eq(featureFlags.toolName, tool_name))
      : and(isNull(featureFlags.organizationId), eq(featureFlags.toolName, tool_name));

    const existing = await db
      .select({ id: featureFlags.id })
      .from(featureFlags)
      .where(condition)
      .limit(1);

    const updates: Record<string, unknown> = { updatedAt: new Date() };
    if (enabled !== undefined) updates.enabled = enabled;
    if (requires_approval !== undefined) updates.requiresApproval = requires_approval;

    if (existing.length > 0) {
      await db.update(featureFlags).set(updates).where(eq(featureFlags.id, existing[0].id));
    } else {
      await db.insert(featureFlags).values({
        organizationId: orgIdValue,
        toolName: tool_name,
        displayName: tool_name,
        category: "system",
        enabled: enabled ?? true,
        requiresApproval: requires_approval ?? false,
        riskLevel: "medium",
      });
    }

    return NextResponse.json({ success: true });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to update setting" },
      { status: 500 }
    );
  }
}
