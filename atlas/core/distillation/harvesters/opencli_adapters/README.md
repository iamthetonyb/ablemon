# OpenCLI Adapters

Each YAML file in this directory teaches the `OpenCLIHarvester` how to
extract conversations from one AI platform.

## YAML schema

```yaml
# Required
platform: "platform_name"          # Unique identifier
description: "What this adapter does"
model_name: "model-name"           # Teacher model tag for training metadata
harvest_method: "file"             # file | command | browser

# For harvest_method: file
file_patterns:                     # Glob patterns (~ is expanded)
  - "~/Downloads/export/*.json"
  - "~/.platform/conversations/*.jsonl"

# Message extraction
message_path: "messages"           # Dot-separated path into JSON.
                                   # Use * to iterate over dict values
                                   # or list elements.
                                   # Example: "mapping.*.message"
                                   # Empty string → obj["messages"]

# Role normalisation
role_mapping:                      # Map platform roles → standard roles
  human: user                      # Only user, assistant, system are kept
  assistant: assistant
  system: system

# Optional
thinking_field: null               # JSON key holding chain-of-thought
                                   # Set to null if the platform doesn't
                                   # expose thinking tokens
```

## Supported harvest methods

| Method | Status | Description |
|--------|--------|-------------|
| `file` | Implemented | Read from local JSON / JSONL exports |
| `command` | Placeholder | Run a shell command to produce JSON output |
| `browser` | Planned | Automate browser export via Playwright |

## Adding a new adapter

1. Create `<platform>.yaml` in this directory following the schema above.
2. The harvester auto-discovers new files on init — no code changes needed.
3. Test: `python -c "from atlas.core.distillation.harvesters.opencli_harvester import OpenCLIHarvester; h = OpenCLIHarvester(); print(list(h.adapters.keys()))"`

## Model name conventions

Use the canonical model name that will appear in training metadata:
- `gpt-5.4` / `gpt-5.4-mini` for OpenAI models
- `claude-opus-4.6` for Anthropic
- `grok-3` for xAI
- Platform name (lowercase) when the exact model is unknown
