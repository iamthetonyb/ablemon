# Skill: Notion

> **Notion Workspace Integration**
> Create, update, and query Notion pages, databases, and blocks.

## Purpose

Integrate with Notion workspaces for content management, note-taking, and database operations. MCP-compatible tool interface.

## Triggers

- Command: "notion"
- Command: "create page"
- Command: "save to notion"
- Command: "add to notion"
- Command: "search notion"
- Command: "update notion"
- Command: "notion database"
- Command: "log this"

## Inputs

| Name | Type | Required | Description |
|------|------|----------|-------------|
| operation | string | Yes | create_page, update_page, query_database, search, append_blocks |
| parent_id | string | Varies | Page or database ID for parent |
| title | string | Varies | Page or database title |
| content | string | No | Markdown content for page |
| properties | object | No | Database properties to set |
| filter | object | No | Query filter for database |

## Outputs

| Name | Type | Description |
|------|------|-------------|
| success | boolean | Whether operation succeeded |
| page_id | string | Created/updated page ID |
| page_url | string | URL to the Notion page |
| results | array | Query results for search/database operations |
| error | string | Error message if failed |

## Dependencies

- aiohttp
- NOTION_API_KEY environment variable

---

## Protocol

> **When this skill triggers, use these capabilities**

### Available Operations

#### 1. Create Page
```
operation: create_page
parent_id: <page_id or database_id>
title: "Page Title"
content: "Markdown content here"
```

#### 2. Update Page
```
operation: update_page
page_id: <existing_page_id>
properties: {title: "New Title", ...}
```

#### 3. Query Database
```
operation: query_database
database_id: <database_id>
filter: {property: "Status", select: {equals: "Done"}}
```

#### 4. Search
```
operation: search
query: "search term"
```

#### 5. Append Blocks
```
operation: append_blocks
page_id: <page_id>
blocks: [
  {type: "paragraph", content: "text"},
  {type: "heading_1", content: "Header"},
  {type: "bulleted_list_item", content: "Item"}
]
```

### Block Types

| Type | Description |
|------|-------------|
| paragraph | Regular text |
| heading_1 | H1 header |
| heading_2 | H2 header |
| heading_3 | H3 header |
| bulleted_list_item | Bullet point |
| numbered_list_item | Numbered item |
| to_do | Checkbox item |
| code | Code block |
| quote | Block quote |
| callout | Callout box |
| divider | Horizontal line |

### Markdown Conversion

The tool automatically converts markdown to Notion blocks:

```markdown
# Heading 1        → heading_1
## Heading 2       → heading_2
### Heading 3      → heading_3
- Item             → bulleted_list_item
1. Item            → numbered_list_item
- [ ] Todo         → to_do (unchecked)
- [x] Done         → to_do (checked)
> Quote            → quote
```code```         → code
---                → divider
```

### Property Types

For database operations:

| Type | Example |
|------|---------|
| title | `{title: [{text: {content: "Title"}}]}` |
| rich_text | `{rich_text: [{text: {content: "Text"}}]}` |
| number | `{number: 42}` |
| select | `{select: {name: "Option"}}` |
| multi_select | `{multi_select: [{name: "Tag1"}, {name: "Tag2"}]}` |
| date | `{date: {start: "2024-01-15"}}` |
| checkbox | `{checkbox: true}` |
| url | `{url: "https://..."}` |
| status | `{status: {name: "In Progress"}}` |

---

## Examples

**Save meeting notes:**
```
User: Save this to Notion: Meeting with client about Q1 goals
→ operation: create_page
→ title: "Meeting with client about Q1 goals"
→ content: (extracted from context)
```

**Query tasks:**
```
User: Show me all open tasks in Notion
→ operation: query_database
→ filter: {property: "Status", status: {does_not_equal: "Done"}}
```

**Append to daily log:**
```
User: Log this: Completed the deployment
→ operation: append_blocks
→ page_id: (today's daily log page)
→ blocks: [{type: "paragraph", content: "Completed the deployment"}]
```

---

## Configuration

Requires `NOTION_API_KEY` environment variable.

To get an API key:
1. Go to https://www.notion.so/my-integrations
2. Create new integration
3. Copy the "Internal Integration Token"
4. Add to ~/.atlas/.secrets/NOTION_API_KEY

The integration must be added to any pages/databases you want to access.
