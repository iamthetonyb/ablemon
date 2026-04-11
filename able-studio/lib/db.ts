import { neon } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-http";
import type { NeonHttpDatabase } from "drizzle-orm/neon-http";
import * as schema from "@/drizzle/schema";

let _db: NeonHttpDatabase<typeof schema> | null = null;

function getDb(): NeonHttpDatabase<typeof schema> {
  if (!_db) {
    const url = process.env.DATABASE_URL;
    if (!url) {
      throw new Error(
        "DATABASE_URL is not set — add it in Vercel project settings or .env"
      );
    }
    _db = drizzle(neon(url), { schema });
  }
  return _db;
}

/** True when DATABASE_URL is configured and DB can be used. */
export function isDbConfigured(): boolean {
  return !!process.env.DATABASE_URL;
}

// Lazy proxy — neon() only fires on first actual query (runtime),
// never at module evaluation (build time). Zero-change for importers.
export const db = new Proxy({} as NeonHttpDatabase<typeof schema>, {
  get(_, prop) {
    return (getDb() as unknown as Record<string | symbol, unknown>)[prop];
  },
});
