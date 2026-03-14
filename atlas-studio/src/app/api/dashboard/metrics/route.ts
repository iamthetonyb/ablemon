import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { projects, tasks, contacts, deals, auditLogs, featureFlags } from "@/db/schema";
import { eq, sql, gte, and } from "drizzle-orm";

export async function GET() {
  try {
    const now = new Date();
    const dayAgo = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    const weekAhead = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);

    const [projectCount] = await db.select({ count: sql<number>`count(*)` }).from(projects).where(eq(projects.status, "active"));
    const [taskCount] = await db.select({ count: sql<number>`count(*)` }).from(tasks);
    const [completedToday] = await db.select({ count: sql<number>`count(*)` }).from(tasks).where(and(eq(tasks.lane, "done"), gte(tasks.updatedAt, dayAgo)));
    const [deadlineCount] = await db.select({ count: sql<number>`count(*)` }).from(tasks).where(and(sql`${tasks.dueDate} IS NOT NULL`, sql`${tasks.dueDate} <= ${weekAhead}`, sql`${tasks.lane} != 'done'`));
    const [contactCount] = await db.select({ count: sql<number>`count(*)` }).from(contacts);
    const [dealCount] = await db.select({ count: sql<number>`count(*)` }).from(deals).where(sql`${deals.stage} NOT IN ('closed_won', 'closed_lost')`);
    const [auditCount] = await db.select({ count: sql<number>`count(*)` }).from(auditLogs).where(gte(auditLogs.createdAt, dayAgo));
    const [toolCount] = await db.select({ count: sql<number>`count(*)` }).from(featureFlags).where(eq(featureFlags.enabled, true));

    return NextResponse.json({
      activeProjects: Number(projectCount.count),
      totalTasks: Number(taskCount.count),
      completedToday: Number(completedToday.count),
      upcomingDeadlines: Number(deadlineCount.count),
      totalContacts: Number(contactCount.count),
      openDeals: Number(dealCount.count),
      recentAuditCount: Number(auditCount.count),
      activeTools: Number(toolCount.count),
    });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
