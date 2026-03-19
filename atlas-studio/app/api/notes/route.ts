import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { notes } from "@/drizzle/schema";
import { eq, desc, and, ilike } from "drizzle-orm";

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const category = searchParams.get("category");
  const search = searchParams.get("search");

  try {
    let query = db
      .select({
        id: notes.id,
        title: notes.title,
        content: notes.content,
        category: notes.category,
        pinned: notes.pinned,
        source: notes.source,
        tags: notes.tags,
        createdAt: notes.createdAt,
        updatedAt: notes.updatedAt,
        organizationId: notes.organizationId,
      })
      .from(notes)
      .orderBy(desc(notes.pinned), desc(notes.updatedAt))
      .limit(200)
      .$dynamic();

    if (category) {
      query = query.where(eq(notes.category, category));
    }

    const noteList = await query;

    // Filter by search client-side for simplicity (ilike on content+title)
    let filtered = noteList;
    if (search) {
      const q = search.toLowerCase();
      filtered = noteList.filter(
        (n) =>
          n.title.toLowerCase().includes(q) ||
          n.content.toLowerCase().includes(q)
      );
    }

    return NextResponse.json({ notes: filtered });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const body = await req.json();

  if (!body.title || !body.content) {
    return NextResponse.json(
      { error: "title and content required" },
      { status: 400 }
    );
  }

  try {
    const [note] = await db
      .insert(notes)
      .values({
        title: body.title,
        content: body.content,
        category: body.category || "note",
        pinned: body.pinned || false,
        source: body.source || "manual",
        tags: body.tags || null,
        organizationId: body.organization_id || null,
      })
      .returning();

    return NextResponse.json({ success: true, note });
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
    if (body.content !== undefined) updates.content = body.content;
    if (body.category !== undefined) updates.category = body.category;
    if (body.pinned !== undefined) updates.pinned = body.pinned;
    if (body.tags !== undefined) updates.tags = body.tags;

    const [updated] = await db
      .update(notes)
      .set(updates)
      .where(eq(notes.id, body.id))
      .returning();

    return NextResponse.json({ success: true, note: updated });
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
    await db.delete(notes).where(eq(notes.id, id));
    return NextResponse.json({ success: true });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
