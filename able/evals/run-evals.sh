#!/usr/bin/env bash
# Run ABLE promptfoo evals — per-skill (no cross-product) or all at once
#
# Usage:
#   ./run-evals.sh                    # All 3 skills, Sonnet 4.6 grading (distillation)
#   ./run-evals.sh --strict           # All 3 skills, GPT 5.4 mini grading (quality gate)
#   ./run-evals.sh copywriting        # Single skill
#   ./run-evals.sh --strict security  # Single skill, strict grading
#   ./run-evals.sh --parallel         # All 3 in parallel
#
# Grading modes:
#   Default  → Sonnet 4.6 (consistent tier ranking, best for distillation signal)
#   --strict → GPT 5.4 mini (harsh quality gates, catches edge cases)
#
# Total: 60 API calls (20 tests × 3 providers), no cross-product waste

set -euo pipefail
cd "$(dirname "$0")"

# Source API key
if [ -f "../.env" ]; then
  export $(grep OPENROUTER_API_KEY ../.env | xargs) 2>/dev/null
fi

# Activate correct node version
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm use 20.19 2>/dev/null || true

STRICT=false
SKILL=""
PARALLEL=false

# Parse args
for arg in "$@"; do
  case "$arg" in
    --strict)   STRICT=true ;;
    --parallel) PARALLEL=true ;;
    copywriting|security|refactoring) SKILL="$arg" ;;
    all) SKILL="" ;;
    *) echo "Usage: $0 [--strict] [--parallel] [copywriting|security|refactoring|all]"; exit 1 ;;
  esac
done

suffix=""
grader_label="Sonnet 4.6"
if [ "$STRICT" = true ]; then
  suffix="-strict"
  grader_label="GPT 5.4 mini"
fi

EVALS=()
if [ -n "$SKILL" ]; then
  case "$SKILL" in
    copywriting) EVALS=("eval-copywriting${suffix}.yaml") ;;
    security)    EVALS=("eval-security${suffix}.yaml") ;;
    refactoring) EVALS=("eval-code-refactoring${suffix}.yaml") ;;
  esac
else
  EVALS=(
    "eval-copywriting${suffix}.yaml"
    "eval-security${suffix}.yaml"
    "eval-code-refactoring${suffix}.yaml"
  )
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  ABLE Eval Suite | Grading: $grader_label"
echo "═══════════════════════════════════════════════════════════"

run_eval() {
  local config="$1"
  local name="${config%.yaml}"
  echo ""
  echo "  ▸ Running: $name"
  echo "  ─────────────────────────────────────"
  npx promptfoo@latest eval -c "$config" --no-cache --max-concurrency 3
  echo "  ✓ $name complete"
}

if [ "$PARALLEL" = true ]; then
  echo "  Mode: parallel"
  for config in "${EVALS[@]}"; do
    run_eval "$config" &
  done
  wait
else
  for config in "${EVALS[@]}"; do
    run_eval "$config"
  done
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  All evals complete."
echo "  View:   npx promptfoo@latest view"
echo "  AGI:    python3 collect_results.py"
echo "═══════════════════════════════════════════════════════════"
