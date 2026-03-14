import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { activities } from "@/db/schema";
import { desc } from "drizzle-orm";

export async function GET() {
  try {
    const rows = await db.select().from(activities).orderBy(desc(activities.createdAt)).limit(30);
    return NextResponse.json({ activities: rows });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
