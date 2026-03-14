import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Navbar } from "../components/navbar";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "ATLAS Mission Control",
  description: "Agent tracking, client portals, and memory management.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${inter.className} min-h-screen selection:bg-gold/30 selection:text-gold`}>
        <Navbar />
        <main className="max-w-[1600px] mx-auto px-4 md:px-8 py-8 pt-24">
          {children}
        </main>
      </body>
    </html>
  );
}
