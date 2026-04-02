Run promptfoo evaluations to test skill quality across models.

Usage:
1. Navigate to eval directory: able/evals/
2. Run all evals: `npx promptfoo@latest eval`
3. View results: `npx promptfoo@latest view`
4. Run specific test: `npx promptfoo@latest eval --filter-pattern "copywriting"`

Available test suites:
- copywriting-tests.yaml — Forbidden lexicon, CTA presence, framework usage
- security-tests.yaml — No hardcoded secrets, parameterized queries, bcrypt
- code-refactoring-tests.yaml — SOLID adherence, behavior preservation

Providers configured:
- OpenRouter (Qwen 397B) — primary
- Anthropic (Claude Sonnet 4) — secondary

To add a new test suite:
1. Create able/evals/prompts/{skill}.txt with the prompt template
2. Create able/evals/tests/{skill}-tests.yaml with test cases
3. Add both to able/evals/promptfooconfig.yaml
4. Run eval to validate

Reference: able/evals/promptfooconfig.yaml
