---
name: atlas-notion
description: Notion workspace integration — create pages, query databases, append blocks, search. Requires NOTION_API_KEY. Triggers on notion, create page, save to notion, log this.
user-invocable: true
---

# /atlas-notion $ARGUMENTS

Notion operation: **$ARGUMENTS**

## Operations
- **create_page**: Create a new page with title and content
- **query_database**: Query a database with filters
- **append_blocks**: Add content to existing page
- **search**: Search across workspace

## Usage Examples
- `/atlas-notion create page "Meeting Notes" with today's summary`
- `/atlas-notion search "project roadmap"`
- `/atlas-notion log "Completed deployment to production"`

## Requirements
- `NOTION_API_KEY` in `~/.atlas/.secrets/`
- Integration must be added to target pages/databases in Notion

Reference: `atlas/skills/library/notion/SKILL.md` for full protocol including block types and property formats.
