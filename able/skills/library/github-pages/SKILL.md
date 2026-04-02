# GitHub Pages Skill

## Purpose
Deploy static HTML/CSS/JS sites to GitHub Pages. No servers, no databases — pure static hosting.

## When to Use
Trigger when the user says:
- "deploy a site", "host on GitHub", "GitHub Pages"
- "publish HTML", "static site", "make it live"
- "put it online", "deploy a landing page"

## When NOT to Use
- The site needs a backend / database → Use Vercel (serverless) or DO (VPS)
- The site uses React/Next.js and needs SSR → Use Vercel
- The site needs environment variables at runtime → Use Vercel or DO

## Protocol

### File Structure
GitHub Pages serves from the root of `gh-pages` branch (or `/docs` on main):
```
gh-pages branch:
  index.html          ← required entry point
  style.css
  script.js
  assets/
    images/
```

### Pages URL Format
```
https://{owner}.github.io/{repo}/
```
Example: `https://iamthetonyb.github.io/able-demo/`

If the repo name matches `{owner}.github.io`, it serves at the root domain.

### Limitations
- Static files only (HTML, CSS, JS, images, fonts)
- No server-side execution
- No environment variables at runtime
- Bandwidth limits on free tier (100GB/month soft limit)
- Build time: typically 30–90 seconds after push

### Deployment Steps
1. Ensure repo exists (create if needed)
2. Push HTML/CSS/JS files to `gh-pages` branch
3. Enable GitHub Pages via API (source: `gh-pages` branch, path: `/`)
4. Wait 30–90 seconds for Pages to build
5. Return live URL to user

## Approval Required
Requires owner approval (low risk — public static content only).
