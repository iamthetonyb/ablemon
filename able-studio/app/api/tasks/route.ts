import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { tasks } from "@/drizzle/schema";
import { eq, desc, count, sql } from "drizzle-orm";

export async function GET() {
  try {
    const taskList = await db
      .select({
        id: tasks.id,
        title: tasks.title,
        description: tasks.description,
        status: tasks.status,
        priority: tasks.priority,
        assignee: tasks.assignee,
        dueDate: tasks.dueDate,
        tags: tasks.tags,
        createdAt: tasks.createdAt,
        updatedAt: tasks.updatedAt,
        organizationId: tasks.organizationId,
      })
      .from(tasks)
      .orderBy(desc(tasks.updatedAt))
      .limit(200);

    const statusCounts = await db
      .select({ status: tasks.status, count: count() })
      .from(tasks)
      .groupBy(tasks.status);

    return NextResponse.json({ tasks: taskList, statusCounts });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const body = await req.json();

  if (!body.title) {
    return NextResponse.json({ error: "title required" }, { status: 400 });
  }

  try {
    const [task] = await db
      .insert(tasks)
      .values({
        title: body.title,
        description: body.description || null,
        status: body.status || "backlog",
        priority: body.priority || "medium",
        assignee: body.assignee || null,
        dueDate: body.due_date ? new Date(body.due_date) : null,
        tags: body.tags || null,
        organizationId: body.organization_id || null,
      })
      .returning();

    return NextResponse.json({ success: true, task });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function PUT(req: NextRequest) {
  const body = await req.json();

  if (!body.id) {
    return NextResponse.json({ error: "id required" }, { status: 400 });
  }

  try {
    const updates: Record<string, any> = { updatedAt: new Date() };
    if (body.title !== undefined) updates.title = body.title;
    if (body.description !== undefined) updates.description = body.description;
    if (body.status !== undefined) updates.status = body.status;
    if (body.priority !== undefined) updates.priority = body.priority;
    if (body.assignee !== undefined) updates.assignee = body.assignee;
    if (body.tags !== undefined) updates.tags = body.tags;

    const [updated] = await db
      .update(tasks)
      .set(updates)
      .where(eq(tasks.id, body.id))
      .returning();

    return NextResponse.json({ success: true, task: updated });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function DELETE(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const id = searchParams.get("id");

  if (!id) {
    return NextResponse.json({ error: "id required" }, { status: 400 });
  }

  try {
    await db.delete(tasks).where(eq(tasks.id, id));
    return NextResponse.json({ success: true });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
