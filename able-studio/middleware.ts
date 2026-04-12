/**
 * NextAuth session enforcement middleware.
 *
 * Protects page routes only — /dashboard/*, /settings/*, /admin/*.
 * API routes are NOT in the matcher: AJAX calls can't follow redirects,
 * so auth on API routes must be checked in the route handler itself.
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
  matcher: ["/dashboard/:path*", "/settings/:path*", "/admin/:path*"],
};
