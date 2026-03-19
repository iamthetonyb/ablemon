import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { organizations, auditLogs, featureFlags, users } from "@/drizzle/schema";
import { count, sql, desc, eq } from "drizzle-orm";

export async function GET() {
  try {
    // Org count
    const [orgCount] = await db.select({ count: count() }).from(organizations);

    // User count
    const [userCount] = await db.select({ count: count() }).from(users);

    // Audit log stats (last 24h)
    const [auditCount] = await db
      .select({ count: count() })
      .from(auditLogs)
      .where(sql`${auditLogs.createdAt} > now() - interval '24 hours'`);

    // Total tokens + cost
    const [tokenStats] = await db
      .select({
        totalInput: sql<number>`coalesce(sum(${auditLogs.inputTokens}), 0)`,
        totalOutput: sql<number>`coalesce(sum(${auditLogs.outputTokens}), 0)`,
        totalCost: sql<number>`coalesce(sum(${auditLogs.costCents}), 0)`,
      })
      .from(auditLogs);

    // Enabled tools count
    const [toolCount] = await db
      .select({ count: count() })
      .from(featureFlags)
      .where(eq(featureFlags.enabled, true));

    // Recent audit logs (last 10)
    const recentLogs = await db
      .select({
        id: auditLogs.id,
        task: auditLogs.task,
        agentRole: auditLogs.agentRole,
        status: auditLogs.status,
        severity: auditLogs.severity,
        providerUsed: auditLogs.providerUsed,
        modelUsed: auditLogs.modelUsed,
        inputTokens: auditLogs.inputTokens,
        outputTokens: auditLogs.outputTokens,
        durationMs: auditLogs.durationMs,
        createdAt: auditLogs.createdAt,
      })
      .from(auditLogs)
      .orderBy(desc(auditLogs.createdAt))
      .limit(10);

    // Organizations with plan distribution
    const orgList = await db
      .select({
        id: organizations.id,
        name: organizations.name,
        slug: organizations.slug,
        plan: organizations.plan,
        createdAt: organizations.createdAt,
      })
      .from(organizations)
      .orderBy(desc(organizations.createdAt))
      .limit(20);

    return NextResponse.json({
      metrics: {
        organizations: orgCount.count,
        users: userCount.count,
        auditEvents24h: auditCount.count,
        enabledTools: toolCount.count,
        totalInputTokens: Number(tokenStats.totalInput),
        totalOutputTokens: Number(tokenStats.totalOutput),
        totalCostCents: Number(tokenStats.totalCost),
      },
      recentLogs,
      organizations: orgList,
    });
  } catch (e: any) {
    console.error("Dashboard API error:", e);
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
