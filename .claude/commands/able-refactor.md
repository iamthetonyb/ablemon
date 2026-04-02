Refactor code using the ABLE Code Refactoring skill.

Follow the code-refactoring SKILL.md protocol:
1. Document pre-refactor snapshot (file, public API, callers, complexity hotspots)
2. Apply SOLID analysis
3. Extract methods (functions < 30 lines)
4. Remove duplication (DRY)
5. Run smoke tests after each extraction
6. Document post-refactor validation (API unchanged, no circular imports, line counts)

Constraints:
- MUST preserve all existing public APIs
- MUST NOT change behavior during refactoring
- MUST NOT remove "unused" code without searching for callers
- Commit after each logical extraction

Reference: able/skills/library/code-refactoring/SKILL.md
