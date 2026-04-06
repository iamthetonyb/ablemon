---
name: able-deploy
description: Deploy ABLE to the production server. Runs deploy script, verifies via GitHub Actions, checks health endpoint. Use for pushing updates live.
user-invocable: true
---

# /able-deploy $ARGUMENTS

Deploy ABLE updates to production.

## Deployment Flow

### Option A: Git Push (Primary)
1. Ensure all changes are committed
2. Push to `main` branch — triggers GitHub Actions deploy workflow
3. Monitor: `gh run list --limit 3`
4. Verify: check health endpoint after deploy

### Option B: Direct Deploy
```bash
bash deploy-to-server.sh
```

### Option C: Docker Deploy
```bash
cd able && bash deploy.sh
```

## Pre-Deploy Checklist
- [ ] All changes committed and pushed
- [ ] No failing tests
- [ ] Security scan clean
- [ ] **Codex cross-audit PASS** (run below)
- [ ] Environment variables set on server (`.secrets/`)
- [ ] `requirements.txt` up to date if deps changed

### Codex Cross-Audit (Pre-Merge Gate)
Before deploying, run an independent code audit on the diff:
```bash
python3 -m able.tools.codex_audit HEAD~1..HEAD
```
Or from Python:
```python
from able.tools.codex_audit import audit_diff
result = await audit_diff(commit_range="HEAD~5..HEAD")
if result.verdict == "FAIL":
    print(f"BLOCKED: {result.critical_count} critical findings")
    for f in result.findings:
        print(f"  [{f.severity}] {f.description}")
```
- **PASS**: proceed to deploy
- **PARTIAL**: review warnings, deploy at discretion
- **FAIL**: fix critical findings before deploying

## Post-Deploy Verification
1. Check GitHub Actions: `gh run list --limit 1`
2. Check health endpoint: `curl https://[server]/health`
3. Check Telegram bot responds (if enabled)
4. Check audit log for errors

## Rollback
If deploy fails:
```bash
git revert HEAD
git push
```

$ARGUMENTS can specify: "check" (just verify), "docker" (docker deploy), or empty for standard git push deploy.
