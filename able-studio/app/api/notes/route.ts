import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { notes } from "@/drizzle/schema";
import { desc, eq, and } from "drizzle-orm";

export async function GET(req: NextRequest) {
  try {
    const { searchParams } = req.nextUrl;
    const category = searchParams.get("category");
    const orgId = searchParams.get("org_id");

    const conditions = [];
    if (category) conditions.push(eq(notes.category, category));
    if (orgId) conditions.push(eq(notes.organizationId, orgId));

    const rows = await db
      .select()
      .from(notes)
      .where(conditions.length > 0 ? and(...conditions) : undefined)
      .orderBy(desc(notes.pinned), desc(notes.updatedAt));

    return NextResponse.json({
      notes: rows.map((n) => ({
        ...n,
        createdAt: n.createdAt.toISOString(),
        updatedAt: n.updatedAt.toISOString(),
      })),
    });
  } catch (error) {
    console.error("Failed to load notes:", error);
    return NextResponse.json(
      { error: "Failed to load notes" },
      { status: 500 }
    );
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { title, content, category, source, tags, pinned, org_id } = body;

    if (!title) {
      return NextResponse.json({ error: "title required" }, { status: 400 });
    }

    const [inserted] = await db
      .insert(notes)
      .values({
        organizationId: org_id ?? null,
        title,
        content: content ?? "",
        category: category ?? "note",
        source: source ?? "manual",
        tags: tags ?? [],
        pinned: pinned ?? false,
      })
      .returning();

    return NextResponse.json({
      success: true,
      note: {
        ...inserted,
        createdAt: inserted.createdAt.toISOString(),
        updatedAt: inserted.updatedAt.toISOString(),
      },
    });
  } catch (error) {
    console.error("Failed to create note:", error);
    return NextResponse.json(
      { error: "Failed to create note" },
      { status: 500 }
    );
  }
}

export async function PUT(req: NextRequest) {
  try {
    const body = await req.json();
    const { id, title, content, pinned, category, tags } = body;

    if (!id) {
      return NextResponse.json({ error: "id required" }, { status: 400 });
    }

    const updates: Record<string, unknown> = { updatedAt: new Date() };
    if (title !== undefined) updates.title = title;
    if (content !== undefined) updates.content = content;
    if (pinned !== undefined) updates.pinned = pinned;
    if (category !== undefined) updates.category = category;
    if (tags !== undefined) updates.tags = tags;

    const [updated] = await db
      .update(notes)
      .set(updates)
      .where(eq(notes.id, id))
      .returning();

    if (!updated) {
      return NextResponse.json({ error: "note not found" }, { status: 404 });
    }

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Failed to update note:", error);
    return NextResponse.json(
      { error: "Failed to update note" },
      { status: 500 }
    );
  }
}

export async function DELETE(req: NextRequest) {
  try {
    const { searchParams } = req.nextUrl;
    const id = searchParams.get("id");

    if (!id) {
      return NextResponse.json({ error: "id required" }, { status: 400 });
    }

    await db.delete(notes).where(eq(notes.id, id));

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Failed to delete note:", error);
    return NextResponse.json(
      { error: "Failed to delete note" },
      { status: 500 }
    );
  }
}
