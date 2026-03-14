import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { tasks } from "@/db/schema";
import { desc, sql } from "drizzle-orm";

export async function GET() {
  try {
    const rows = await db
      .select({ id: tasks.id, title: tasks.title, priority: tasks.priority, dueDate: tasks.dueDate })
      .from(tasks)
      .where(sql`${tasks.lane} != 'done'`)
      .orderBy(desc(tasks.priority), tasks.dueDate)
      .limit(10);
    return NextResponse.json({ tasks: rows });
  } catch (e: any) {
    return NextResponse.json({ error: e.message }, { status: 500 });
  }
}
