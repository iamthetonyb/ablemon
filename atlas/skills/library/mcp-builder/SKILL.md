---
name: mcp-builder
description: "Build MCP (Model Context Protocol) servers and tools. Use when creating new tool integrations, API wrappers, or extending ATLAS capabilities via the MCP protocol. Triggers on: MCP, build MCP server, create tool, tool integration, API wrapper, extend capabilities."
---

# MCP Builder

> Design and implement MCP servers following the 4-phase protocol.

## Overview

MCP (Model Context Protocol) servers expose tools, resources, and prompts to AI agents via a standardized JSON-RPC protocol. Use this skill when extending ATLAS with new integrations.

## Process

### Phase 1: Research and Planning
1. **Identify the API/service** to integrate
2. **Read API documentation** thoroughly
3. **Map API endpoints → MCP tools** (what actions should be exposed?)
4. **Define resources** (what data should be queryable?)
5. **Plan authentication** (API keys, OAuth, etc.)

### Phase 2: Implementation

```python
# Minimal MCP server structure
from mcp.server import Server
from mcp.types import Tool, Resource

server = Server("my-integration")

@server.tool()
async def my_tool(param: str) -> str:
    """Description of what this tool does."""
    # Implementation
    return result

@server.resource("resource://my-data")
async def my_resource() -> str:
    """Expose data as a resource."""
    return data
```

#### Key Patterns
- **Tools**: Actions the AI can take (read, write, search)
- **Resources**: Data the AI can query (docs, schemas, configs)
- **Prompts**: Pre-built prompt templates for common tasks

### Phase 3: Test
- Test each tool individually with mock inputs
- Test error handling (network failures, auth errors, rate limits)
- Test with actual AI agent to verify tool descriptions trigger correctly

### Phase 4: Evaluate
- Use promptfoo to compare tool usage across models
- Verify tools trigger on expected user inputs
- Check output format consistency

## Quality Gates

- [ ] All tools have clear, descriptive names
- [ ] Tool descriptions explain WHEN to use (not just what)
- [ ] Error messages are actionable
- [ ] Rate limiting respected
- [ ] Authentication handled securely (env vars, not hardcoded)
- [ ] Tests pass with mock and live API

## References
- [MCP Specification](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP TypeScript SDK](https://github.com/modelcontextprotocol/typescript-sdk)
