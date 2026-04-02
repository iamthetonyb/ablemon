---
name: corpus-generator
description: "Generate training data for model distillation by running prompts from the prompt bank through a teacher model and saving responses in ChatML format."
type: hybrid
trust_level: L3_ACT
triggers:
  - "generate corpus"
  - "generate training data"
  - "build corpus"
  - "distillation data"
---

# Corpus Generator

Generate high-quality training data for ABLE model distillation.

## Purpose

Drive active corpus generation sessions using the prompt bank to produce teacher-model response pairs for fine-tuning Qwen 3.5 local models.

## Triggers

- "generate corpus"
- "generate training data"
- "build corpus"
- "distillation data"
- "run distillation prompts"

## Trust Required

**L3** (Act) — Writes training data files to disk.

## Inputs

| Name | Type | Required | Description |
|------|------|----------|-------------|
| domain | string | no | Filter prompts by domain (coding, security, reasoning, creative, agentic) |
| difficulty | string | no | Filter by difficulty (easy, medium, hard) |
| count | int | no | Number of prompts to run (default: 10) |
| from_failures | bool | no | Generate prompts from known failure patterns instead |

## Outputs

| Name | Type | Description |
|------|------|-------------|
| pairs | list | Training pairs in ChatML format |
| stats | dict | Count by domain, difficulty, quality score distribution |
| output_path | string | Path to generated JSONL file |

## Usage

- `/generate-corpus --domain coding --count 25` -- Generate 25 coding prompts
- `/generate-corpus --domain security --difficulty hard --count 10` -- Hard security prompts
- `/generate-corpus --from-failures --count 15` -- Generate from known failure patterns
- `/generate-corpus --status` -- Show corpus statistics

## Process

1. Load prompts from the prompt bank (`able/core/distillation/prompt_bank.py`)
2. Sample prompts matching the requested domain/difficulty filters
3. Present each prompt to the current Claude session
4. Auto-save responses in ChatML training format with quality scoring
5. Tag with `source="corpus_generator"`, `teacher_model` from current session
6. Store in distillation staging area (`data/distillation_corpus.jsonl`)

## Quality Criteria

- Responses must be substantive (>100 tokens for coding, >50 for creative)
- Reasoning should be explicit (step-by-step for hard prompts)
- Tool use should be demonstrated where applicable
- All thinking tokens preserved for distillation

## Error Handling

| Error | Response |
|-------|----------|
| Empty prompt bank | Report count, suggest adding prompts |
| Model refuses prompt | Skip, log refusal, continue with next |
| Low quality response | Flag for review, include but mark as needs_review |
| Disk write failure | Buffer in memory, retry, alert operator |

## Notes

- Prompt bank data lives in `able/core/distillation/prompt_bank_data/`
- Training pairs accumulate in `data/distillation_*.jsonl`
- Target: 100-200 pairs before H100 fine-tuning run
- Current count tracked via `/generate-corpus --status`
