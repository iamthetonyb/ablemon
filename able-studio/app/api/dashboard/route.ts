import { NextResponse } from "next/server";
import { db, isDbConfigured } from "@/lib/db";
import { auditLogs, organizations, featureFlags } from "@/drizzle/schema";
import { desc, gte, count, sum, and, isNotNull, sql } from "drizzle-orm";
import { getResources } from "@/lib/control-plane";

const EMPTY_DASHBOARD = {
  metrics: {
    organizations: 0,
    enabledTools: 0,
    auditEvents24h: 0,
    totalInputTokens: 0,
    totalOutputTokens: 0,
    totalCostCents: 0,
  },
  recentLogs: [],
  organizations: [],
  timestamp: new Date().toISOString(),
};

export async function GET() {
  // Graceful degradation when DATABASE_URL isn't configured
  if (!isDbConfigured()) {
    return NextResponse.json({
      ...EMPTY_DASHBOARD,
      _status: "unconfigured",
      _message: "DATABASE_URL not set — add it in Vercel project settings",
    });
  }

  try {
    const since24h = new Date(Date.now() - 24 * 60 * 60 * 1000);

    const [auditStats, orgList, toolStats, recentLogs] = await Promise.all([
      // Last 24h audit stats
      db
        .select({
          count: count(),
          totalInputTokens: sum(auditLogs.inputTokens),
          totalOutputTokens: sum(auditLogs.outputTokens),
          totalCostCents: sum(auditLogs.costCents),
        })
        .from(auditLogs)
        .where(gte(auditLogs.createdAt, since24h)),

      // All organizations
      db
        .select({ id: organizations.id, name: organizations.name, slug: organizations.slug, plan: organizations.plan })
        .from(organizations)
        .orderBy(desc(organizations.createdAt))
        .limit(20),

      // Enabled tool count (global flags)
      db
        .select({ count: count() })
        .from(featureFlags)
        .where(
          and(
            isNotNull(featureFlags.organizationId),
            sql`${featureFlags.enabled} = true`
          )
        ),

      // Recent audit logs for activity feed
      db
        .select({
          id: auditLogs.id,
          runId: auditLogs.runId,
          agentRole: auditLogs.agentRole,
          task: auditLogs.task,
          providerUsed: auditLogs.providerUsed,
          inputTokens: auditLogs.inputTokens,
          outputTokens: auditLogs.outputTokens,
          costCents: auditLogs.costCents,
          durationMs: auditLogs.durationMs,
          severity: auditLogs.severity,
          status: auditLogs.status,
          createdAt: auditLogs.createdAt,
        })
        .from(auditLogs)
        .orderBy(desc(auditLogs.createdAt))
        .limit(20),
    ]);

    const stats = auditStats[0];

    // Try to get resource count from control plane (non-fatal)
    let resourceCount = 0;
    try {
      const resources = await getResources();
      resourceCount = (resources.resources as unknown[]).length;
    } catch {
      // Gateway might not be running
    }

    return NextResponse.json({
      metrics: {
        organizations: orgList.length,
        enabledTools: (toolStats[0]?.count ?? 0) + resourceCount,
        auditEvents24h: stats?.count ?? 0,
        totalInputTokens: Number(stats?.totalInputTokens ?? 0),
        totalOutputTokens: Number(stats?.totalOutputTokens ?? 0),
        totalCostCents: Number(stats?.totalCostCents ?? 0),
      },
      recentLogs: recentLogs.map((log) => ({
        ...log,
        createdAt: log.createdAt.toISOString(),
        durationMs: log.durationMs ?? 0,
        inputTokens: log.inputTokens ?? 0,
        outputTokens: log.outputTokens ?? 0,
      })),
      organizations: orgList,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to load dashboard" },
      { status: 500 }
    );
  }
}
