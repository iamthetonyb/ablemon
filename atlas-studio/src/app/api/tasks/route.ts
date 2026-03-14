import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { tasks } from "@/db/schema";
import { eq } from "drizzle-orm";

export async function GET(req: NextRequest) {
  try {
    const projectId = req.nextUrl.searchParams.get("project_id");
    const rows = projectId
      ? await db.select().from(tasks).where(eq(tasks.projectId, projectId)).orderBy(tasks.sortOrder)
      : await db.select().from(tasks).orderBy(tasks.sortOrder);
    return NextResponse.json({ tasks: rows });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    if (!body.title || !body.project_id) return NextResponse.json({ error: "title and project_id required" }, { status: 400 });
    const [row] = await db.insert(tasks).values({
      projectId: body.project_id,
      title: body.title,
      description: body.description || null,
      lane: body.lane || "backlog",
      priority: body.priority ?? 0,
      dueDate: body.due_date ? new Date(body.due_date) : null,
      tags: body.tags || [],
    }).returning();
    return NextResponse.json({ success: true, task: row });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function PUT(req: NextRequest) {
  try {
    const body = await req.json();
    if (!body.id) return NextResponse.json({ error: "id required" }, { status: 400 });
    const updates: Record<string, any> = { updatedAt: new Date() };
    if (body.title !== undefined) updates.title = body.title;
    if (body.description !== undefined) updates.description = body.description;
    if (body.lane !== undefined) updates.lane = body.lane;
    if (body.priority !== undefined) updates.priority = body.priority;
    if (body.sort_order !== undefined) updates.sortOrder = body.sort_order;
    if (body.due_date !== undefined) updates.dueDate = body.due_date ? new Date(body.due_date) : null;
    if (body.tags !== undefined) updates.tags = body.tags;
    const [row] = await db.update(tasks).set(updates).where(eq(tasks.id, body.id)).returning();
    if (!row) return NextResponse.json({ error: "not found" }, { status: 404 });
    return NextResponse.json({ success: true, task: row });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function DELETE(req: NextRequest) {
  try {
    const { id } = await req.json();
    if (!id) return NextResponse.json({ error: "id required" }, { status: 400 });
    await db.delete(tasks).where(eq(tasks.id, id));
    return NextResponse.json({ success: true });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
