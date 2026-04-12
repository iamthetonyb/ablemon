/**
 * NextAuth session enforcement middleware.
 *
 * Protects ALL page routes — unauthenticated visitors redirect to /login.
 * API routes excluded from matcher (AJAX can't follow redirects).
 */

import { auth } from "@/lib/auth";
import { NextResponse } from "next/server";

export default auth((req) => {
  if (!req.auth) {
    const loginUrl = new URL("/login", req.url);
    loginUrl.searchParams.set("callbackUrl", req.nextUrl.pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
});

export const config = {
  // Match all paths EXCEPT: /api/*, /login, /_next/*, static files
  matcher: ["/((?!api|_next|login|icon\\.svg|favicon\\.ico).*)"],
};
