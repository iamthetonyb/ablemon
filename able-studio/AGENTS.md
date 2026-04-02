<!-- BEGIN:nextjs-agent-rules -->

# Next.js: ALWAYS read docs before coding

Before any Next.js work, find and read the relevant doc in `node_modules/next/dist/docs/`. Your training data is outdated — the docs are the source of truth.

<!-- END:nextjs-agent-rules -->

# ABLE Studio

This is the Next.js 16.2 dashboard for ABLE. Key architecture:

- **App Router** with edge runtime API routes
- **Chat widget**: Lazy-loaded via `ChatBubble` → `ChatPanel` (AI SDK only initializes on user click)
- **Model**: GPT 5.4 Nano via OpenRouter (T1 routing, $0.20/$1.25 per M)
- **Design system**: Gold & Glass dark glassmorphism (see `globals.css`)
- **Auth**: NextAuth v5 + Drizzle + Neon Postgres
- **No Sonnet/Opus calls** — all API routes use OpenRouter with cheap models
