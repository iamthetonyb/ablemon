Build MCP servers using the ABLE MCP Builder skill.

Follow the mcp-builder SKILL.md protocol:
Phase 1: Research — Identify API, read docs, map endpoints → MCP tools, plan auth
Phase 2: Implement — Tools (actions), Resources (data), Prompts (templates)
Phase 3: Test — Individual tools, error handling, AI agent integration
Phase 4: Evaluate — promptfoo comparison, trigger verification, output consistency

Quality gates:
- [ ] Tool names are clear and descriptive
- [ ] Descriptions explain WHEN to use (not just what)
- [ ] Error messages are actionable
- [ ] Rate limiting respected
- [ ] Auth via env vars (never hardcoded)
- [ ] Tests pass with mock and live API

Reference: able/skills/library/mcp-builder/SKILL.md
