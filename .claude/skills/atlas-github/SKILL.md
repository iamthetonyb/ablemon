---
name: atlas-github
description: GitHub operations — create repos, push code, open PRs, trigger workflows. Uses conventional commits and branch naming conventions.
user-invocable: true
---

# /atlas-github $ARGUMENTS

Execute GitHub operations: **$ARGUMENTS**

## Operations

### Create Repository
```bash
gh repo create <name> --public/--private --description "..."
```
- kebab-case names, under 40 chars
- Private for client/business code, public for open source

### Push Code
```bash
git add . && git commit -m "type(scope): summary" && git push
```
Conventional commits: feat, fix, docs, style, refactor, test, chore

### Open PR
```bash
gh pr create --title "..." --body "## What\n## Why\n## How\n## Test"
```

### Branch Naming
- Features: `feat/short-description`
- Fixes: `fix/short-description`
- Deploy: `deploy/environment-name`

## Approval
All write operations require confirmation. Read operations are free.

Reference: `atlas/skills/library/github-integration/SKILL.md` for full protocol.
