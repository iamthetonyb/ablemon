import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { organizations, clientSettings } from "@/drizzle/schema";
import { eq, desc } from "drizzle-orm";
import { encrypt } from "@/lib/encryption";

export async function GET() {
  try {
    const orgs = await db
      .select({
        id: organizations.id,
        name: organizations.name,
        slug: organizations.slug,
        plan: organizations.plan,
        createdAt: organizations.createdAt,
      })
      .from(organizations)
      .orderBy(desc(organizations.createdAt));

    return NextResponse.json({
      organizations: orgs.map((o) => ({
        ...o,
        createdAt: o.createdAt.toISOString(),
      })),
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Failed to load clients" },
      { status: 500 }
    );
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { name, slug } = body;

    if (!name || !slug) {
      return NextResponse.json({ error: "name and slug required" }, { status: 400 });
    }

    // Validate slug format
    if (!/^[a-z0-9-]+$/.test(slug)) {
      return NextResponse.json({ error: "slug must be lowercase alphanumeric with hyphens" }, { status: 400 });
    }

    const [org] = await db
      .insert(organizations)
      .values({ name, slug, plan: "free" })
      .returning();

    // Create default client settings
    await db.insert(clientSettings).values({ organizationId: org.id });

    return NextResponse.json({
      success: true,
      organization: {
        ...org,
        createdAt: org.createdAt.toISOString(),
        updatedAt: org.updatedAt.toISOString(),
      },
    });
  } catch (error) {
    const msg = error instanceof Error ? error.message : "Failed to create client";
    const status = msg.includes("unique") ? 409 : 500;
    return NextResponse.json({ error: msg }, { status });
  }
}

// Update API keys for an organization
export async function PUT(req: NextRequest) {
  try {
    const body = await req.json();
    const { org_id, anthropic_api_key, openrouter_api_key, telegram_bot_token } = body;

    if (!org_id) {
      return NextResponse.json({ error: "org_id required" }, { status: 400 });
    }

    const updates: Record<string, string | null> = {};
    if (anthropic_api_key) updates.anthropicApiKey = await encrypt(anthropic_api_key);
    if (openrouter_api_key) updates.openrouterApiKey = await encrypt(openrouter_api_key);
    if (telegram_bot_token) updates.telegramBotToken = await encrypt(telegram_bot_token);

    if (Object.keys(updates).length === 0) {
      return NextResponse.json({ error: "no keys to update" }, { status: 400 });
    }

    // Upsert client settings
    const existing = await db
      .select({ id: clientSettings.id })
      .from(clientSettings)
      .where(eq(clientSettings.organizationId, org_id))
      .limit(1);

    if (existing.length > 0) {
      await db
        .update(clientSettings)
        .set({ ...updates, updatedAt: new Date() })
        .where(eq(clientSettings.organizationId, org_id));
    } else {
      await db.insert(clientSettings).values({ organizationId: org_id, ...updates });
    }

    return NextResponse.json({ success: true });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Failed to update API keys" },
      { status: 500 }
    );
  }
}
