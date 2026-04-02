# GitHub Integration Skill

## Purpose
Create GitHub repositories, push code, open pull requests, and trigger GitHub Actions workflows via the GitHub REST API v3.

## When to Use
Trigger when the user says:
- "create a repo", "make a repo", "new repo"
- "push code", "push to GitHub", "commit to GitHub"
- "open a PR", "pull request", "create pull request"
- Anything requiring GitHub repository management

## Protocol

### Repo Naming
- Always kebab-case: `my-project`, not `MyProject` or `my_project`
- Descriptive but short (< 40 chars)
- Prefix with context if relevant: `able-`, `client-`, `tool-`

### Branch Naming
- Features: `feat/short-description`
- Fixes: `fix/short-description`
- Deploy: `deploy/environment-name`
- Never push directly to `main` for non-trivial changes

### Commit Message Format (Conventional Commits)
```
<type>(<scope>): <short summary>

Types: feat, fix, docs, style, refactor, test, chore
Examples:
  feat(auth): add OAuth2 login flow
  fix(api): handle 429 rate limit retries
  chore(deps): bump aiohttp to 3.9.1
```

### PR Description Format
```markdown
## What
Brief description of changes.

## Why
Context and motivation.

## How
Key implementation decisions.

## Test
How to verify this works.
```

### Public vs Private Decision
- **Public**: Open source tools, demos, portfolio projects, anything meant to be shared
- **Private**: Client work, business logic, credentials-adjacent code, anything with proprietary data

### When to Trigger Workflows
- GitHub Actions workflows trigger on push automatically if `.github/workflows/*.yml` exists
- Only create a workflow file if the user asks or if CI/CD is clearly needed
- Always use `ubuntu-latest` as the runner

## Approval Required
All write operations (create repo, push, PR) require owner approval.
Read-only operations (list repos) do not require approval.

## Error Handling
- If repo already exists: inform user, offer to use existing repo
- If branch already exists: inform user, ask if they want a different branch name
- If push fails with 409 (conflict): fetch latest SHA and retry once
