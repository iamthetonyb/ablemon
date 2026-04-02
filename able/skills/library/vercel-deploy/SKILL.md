# Vercel Deploy Skill

## Purpose
Deploy React, Next.js frontends and serverless APIs to Vercel's free plan (Hobby tier). Instant global CDN, automatic HTTPS, preview deployments per branch.

## When to Use
Trigger when the user says:
- "deploy to Vercel", "Vercel deploy"
- "deploy an app", "deploy a frontend", "deploy Next.js"
- "host on Vercel"

## Vercel vs DO Decision Guide

| Use Vercel when... | Use DO (VPS) when... |
|---|---|
| React / Next.js / Vue / Svelte | Persistent long-running processes |
| Serverless API functions | Stateful services (databases, queues) |
| CDN-heavy static assets | GPU workloads |
| Preview deployments per PR | Root server access needed |
| Zero-config deploys | Custom networking / firewall rules |
| Free tier is sufficient | > 100GB bandwidth/month |

## Free Plan Limits (Hobby Tier)
- 100GB bandwidth/month
- 100 deployments/day
- Serverless function timeout: 10 seconds
- No team features
- Custom domains: unlimited

## Env Var Handling
- Pass env vars at deployment time via `env_vars` dict
- Sensitive vars should be set in Vercel dashboard, not passed through code
- Vercel auto-injects `VERCEL_URL`, `VERCEL_ENV`, etc.

## Preview vs Production
- Every push creates a **preview** deployment at `{hash}.vercel.app`
- Production deploys are linked to a domain in the Vercel dashboard
- ABLE creates preview deployments; user promotes to production via dashboard

## Deployment Steps
1. Build file list (`{filename: content}` dict)
2. Call `VercelClient.create_deployment()`
3. Poll until `readyState == "READY"`
4. Return live preview URL

## Approval Required
Requires owner approval (low risk — no infrastructure cost on free tier).
