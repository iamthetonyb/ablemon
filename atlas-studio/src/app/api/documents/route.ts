import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { documents } from "@/db/schema";
import { eq, desc } from "drizzle-orm";

export async function GET(req: NextRequest) {
  try {
    const category = req.nextUrl.searchParams.get("category");
    const rows = category
      ? await db.select().from(documents).where(eq(documents.category, category)).orderBy(desc(documents.updatedAt))
      : await db.select().from(documents).orderBy(desc(documents.updatedAt));
    return NextResponse.json({ documents: rows });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    if (!body.title) return NextResponse.json({ error: "title required" }, { status: 400 });
    const [row] = await db.insert(documents).values({
      title: body.title,
      content: body.content || "",
      filePath: body.file_path || null,
      category: body.category || "note",
      pinned: body.pinned ?? false,
      organizationId: body.organization_id || null,
    }).returning();
    return NextResponse.json({ success: true, document: row });
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
    if (body.content !== undefined) updates.content = body.content;
    if (body.category !== undefined) updates.category = body.category;
    if (body.pinned !== undefined) updates.pinned = body.pinned;
    const [row] = await db.update(documents).set(updates).where(eq(documents.id, body.id)).returning();
    if (!row) return NextResponse.json({ error: "not found" }, { status: 404 });
    return NextResponse.json({ success: true, document: row });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function DELETE(req: NextRequest) {
  try {
    const { id } = await req.json();
    if (!id) return NextResponse.json({ error: "id required" }, { status: 400 });
    await db.delete(documents).where(eq(documents.id, id));
    return NextResponse.json({ success: true });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
