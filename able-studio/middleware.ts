/**
 * NextAuth session enforcement middleware.
 *
 * Protects /api/* and /dashboard/* routes — unauthenticated requests
 * redirect to /login. Health and auth endpoints are exempt.
 */

import { auth } from "@/lib/auth";
import { NextResponse } from "next/server";

export default auth((req) => {
  const { pathname } = req.nextUrl;

  // Exempt routes — no auth required
  if (
    pathname.startsWith("/api/auth") ||
    pathname === "/health" ||
    pathname === "/login" ||
    pathname === "/register" ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/favicon") ||
    pathname.match(/\.(ico|png|jpg|svg|css|js|woff2?)$/)
  ) {
    return NextResponse.next();
  }

  // Protected routes — require session
  if (!req.auth) {
    const loginUrl = new URL("/login", req.url);
    loginUrl.searchParams.set("callbackUrl", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
});

export const config = {
  matcher: [
    "/api/:path*",
    "/dashboard/:path*",
    "/settings/:path*",
    "/admin/:path*",
  ],
};
