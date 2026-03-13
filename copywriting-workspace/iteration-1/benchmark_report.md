# Copywriting Skill Benchmark Report — Iteration 1

**Skill tested:** `atlas-copywriting`
**Date:** 2026-03-06
**Evals run:** 4
**Methodology:** Each eval ran with and without the skill active. Outputs scored against assertion checklists.

---

## Summary Scorecard

| Eval | Description | Without Skill | With Skill | Delta |
|------|-------------|--------------|------------|-------|
| 0 | Cold outreach email (forbidden lexicon) | 7/7 — 100% | 6/7 — 86% | **-14%** |
| 1 | NLP meta-programs pitch variants | 7/9 — 78% | 9/9 — 100% | **+22%** |
| 2 | Framework selection (AIDA / PAS / FAB) | 6/8 — 75% | 7/8 — 88% | **+13%** |
| 3 | HoodieFlags World Cup Facebook ad | 9/10 — 90% | 10/10 — 100% | **+10%** |
| **TOTAL** | | **29/34 — 85%** | **32/35 — 91%** | **+6%** |

**Verdict:** Skill wins on 3 of 4 evals. One regression (eval-0). Net improvement: +6 percentage points.

---

## Eval 0 — Cold Outreach Email (Forbidden Lexicon)

**Prompt:** Cold email to Marcus Chen (TaskFlow), target: book 15-min demo.

### Without Skill — 7/7 (100%)

| # | Assertion | Pass |
|---|-----------|------|
| 1 | Zero forbidden lexicon words | ✓ |
| 2 | Does not start with formulaic AI opening | ✓ |
| 3 | Does not use forced triads | ✓ |
| 4 | Does not use contrastive negation pattern | ✓ |
| 5 | Clear CTA to book demo/call | ✓ |
| 6 | Sounds human — contractions, casual rhythm | ✓ |
| 7 | Mentions TaskFlow, pricing page, or lead magnet | ✓ |

**Notes:** Clean, competent cold email. All surface-level assertions pass. The without-skill output reads slightly more generic (lacks PAS structure, NLP targeting, meta-program selection).

### With Skill — 6/7 (86%)

| # | Assertion | Pass |
|---|-----------|------|
| 1 | Zero forbidden lexicon words | ✓ |
| 2 | Does not start with formulaic AI opening | ✓ |
| 3 | Does not use forced triads | ✓ |
| 4 | Does not use contrastive negation pattern | **✗** |
| 5 | Clear CTA to book demo/call | ✓ |
| 6 | Sounds human — contractions, casual rhythm | ✓ |
| 7 | Mentions TaskFlow, pricing page, or lead magnet | ✓ |

**Failure:** The with_skill output uses "That tells me you're not just browsing. You're sizing something up." — a textbook contrastive negation pattern ("not just X. You're Y."). This was flagged as a forbidden pattern.

**Why the regression:** The skill is strong on NLP technique, which sometimes pushes toward using contrast as a rhetorical device. The forbidden pattern rule needs to be stricter in the skill — contrastive negation is appealing but specifically excluded.

---

## Eval 1 — NLP Meta-Programs Pitch Variants

**Prompt:** ShieldAcademy cybersecurity pitch — Variant A (Toward + Internal), Variant B (Away-From + External).

### Without Skill — 7/9 (78%)

| # | Assertion | Pass |
|---|-----------|------|
| 1 | Exactly 2 variants, clearly labeled A and B | ✓ |
| 2 | Variant A labels Toward + Internal | ✓ |
| 3 | Variant B labels Away-From + External | ✓ |
| 4 | Variant A uses toward trigger words, not away-from | ✓ |
| 5 | Variant B uses away-from triggers, not toward | ✓ |
| 6 | Variant A CTA softened for internal reference | **✗** |
| 7 | Variant B CTA uses external validation | ✓ |
| 8 | Zero forbidden lexicon | ✓ |
| 9 | Meta-programs explicitly labeled | ✓ |

**Failure:** Variant A ends with "ShieldAcademy puts you in the driver's seat of your organization's security maturity" — no actual CTA, no "decide for yourself" or "evaluate on your terms" language that satisfies an Internal frame of reference CTA.

### With Skill — 9/9 (100%)

| # | Assertion | Pass |
|---|-----------|------|
| 1 | Exactly 2 variants, clearly labeled A and B | ✓ |
| 2 | Variant A labels Toward + Internal | ✓ |
| 3 | Variant B labels Away-From + External | ✓ |
| 4 | Variant A uses toward trigger words, not away-from | ✓ |
| 5 | Variant B uses away-from triggers, not toward | ✓ |
| 6 | Variant A CTA softened for internal reference | ✓ |
| 7 | Variant B CTA uses external validation | ✓ |
| 8 | Zero forbidden lexicon | ✓ |
| 9 | Meta-programs explicitly labeled | ✓ |

**Skill win:** Variant A CTA ("See how ShieldAcademy fits your strategy — request a walkthrough and decide for yourself.") nails the internal frame of reference. Full meta-program breakdown tables included.

---

## Eval 2 — Framework Selection (AIDA / PAS / FAB)

**Prompt:** ShieldAcademy ad copy — 3 variants using AIDA, PAS, FAB. Target: CTOs post-breach.

### Without Skill — 6/8 (75%)

| # | Assertion | Pass |
|---|-----------|------|
| 1 | Exactly 3 variants using AIDA, PAS, FAB | ✓ |
| 2 | AIDA has labeled Attention/Interest/Desire/Action | ✓ |
| 3 | PAS has labeled Problem/Agitate/Solution | ✓ |
| 4 | FAB has labeled Features/Advantages/Benefits | ✓ |
| 5 | PAS Agitate maps secondary consequences (board, churn, fines, reputation) | **✗** |
| 6 | Zero forbidden lexicon | **✗** |
| 7 | Framework names and section labels shown | ✓ |
| 8 | Each variant is 4-8 sentences | ✓ |

**Failures:**
- Assertion 5: Agitate section mentions regulatory scrutiny and insurance premiums but misses board scrutiny and customer churn. Only 2 of 4 secondary consequence types covered.
- Assertion 6: FAB variant uses "navigating" ("As a CTO navigating the aftermath of a breach") — forbidden lexicon.

### With Skill — 7/8 (88%)

| # | Assertion | Pass |
|---|-----------|------|
| 1 | Exactly 3 variants using AIDA, PAS, FAB | ✓ |
| 2 | AIDA has labeled Attention/Interest/Desire/Action | ✓ |
| 3 | PAS has labeled Problem/Agitate/Solution | ✓ |
| 4 | FAB has labeled Features/Advantages/Benefits | ✓ |
| 5 | PAS Agitate maps secondary consequences | ✓ |
| 6 | Zero forbidden lexicon | ✓ |
| 7 | Framework names and section labels shown | ✓ |
| 8 | Each variant is 4-8 sentences | **✗** |

**Skill win on assertion 5:** Agitate covers all four secondary consequence types: board questioning leadership, customers churning and telling others, regulatory investigations with GDPR/CCPA/SEC fines, and insurer premium hikes or dropped coverage.

**Remaining failure (assertion 8):** The PAS variant runs ~11 sentences (Problem: 2, Agitate: 5, Solution: 4). The skill maximizes consequence depth in the Agitate section but exceeds the length ceiling. Skill should enforce a sentence budget per section.

---

## Eval 3 — HoodieFlags World Cup Facebook Ad

**Prompt:** Facebook ad for HoodieFlags.com, AIDA framework, Orange spiral dynamics, World Cup 2026 urgency.

### Without Skill — 9/10 (90%)

| # | Assertion | Pass |
|---|-----------|------|
| 1 | Three labeled sections: Primary Text, Headline, Description | ✓ |
| 2 | Mentions World Cup 2026 | ✓ |
| 3 | Mentions $49.99 and/or free shipping $75 | ✓ |
| 4 | AIDA structure | ✓ |
| 5 | Orange spiral dynamics language | ✓ |
| 6 | Creates urgency | ✓ |
| 7 | Zero forbidden lexicon | ✓ |
| 8 | No formulaic AI openings or forced triads | ✓ |
| 9 | CTA directing to HoodieFlags.com | ✓ |
| 10 | Headline under 40 characters | **✗** |

**Failure:** Headline "Rep Your Country at World Cup 2026 — Premium Flag Hoodies Starting at $49.99" = 76 characters. Without the skill, the model crams too much product detail into the headline without regard for Facebook's character constraints.

### With Skill — 10/10 (100%)

| # | Assertion | Pass |
|---|-----------|------|
| 1 | Three labeled sections: Primary Text, Headline, Description | ✓ |
| 2 | Mentions FIFA World Cup 2026 | ✓ |
| 3 | Mentions $49.99 and free shipping $75 | ✓ |
| 4 | AIDA structure (explicitly labeled) | ✓ |
| 5 | Orange spiral dynamics language | ✓ |
| 6 | Creates urgency ("97 days out," "before your country sells out") | ✓ |
| 7 | Zero forbidden lexicon | ✓ |
| 8 | No formulaic AI openings or forced triads | ✓ |
| 9 | CTA directing to HoodieFlags.com | ✓ |
| 10 | Headline under 40 characters ("Rep Your Nation. World Cup 2026." = 32 chars) | ✓ |

**Skill win:** Countdown specificity ("97 days out") is a stronger urgency hook than the without_skill's vague "coming to North America." Headline discipline (32 chars vs 76) shows platform-aware formatting.

---

## Key Findings

### Where the skill consistently wins

1. **NLP targeting precision** — The skill explicitly selects and applies meta-programs (Toward/Away-From, Internal/External frame, Sorting Filter, Chunk Size). The without-skill versions identify meta-programs in the label but don't apply them consistently in the body copy, especially in CTAs.

2. **PAS depth** — The skill produces multi-layer agitate sections that map secondary and tertiary consequences, not just restate the problem. This is the hardest thing to get right without the skill.

3. **Platform-specific formatting constraints** — Headline character limits, section labeling, and ad format discipline. The skill applies these; vanilla Claude often ignores them.

4. **Forbidden lexicon discipline** — The skill reliably avoids the full banned list. Without the skill, "navigating" slipped through in eval-2.

### Where the skill needs improvement

1. **Contrastive negation guard** — The skill still allows "not just X. You're Y." constructions. This is a known forbidden pattern that should be explicitly blocked in the skill's revision phase checklist.

2. **Sentence count enforcement** — The PAS framework variant in eval-2 ran 11 sentences. The skill should enforce a ceiling (4-8 sentences per variant) as part of its output validation pass.

### Regression Note — Eval 0

The contrastive negation failure in eval-0 is notable because the without-skill output passed the same test. This is a case where the skill's rhetorical sophistication worked against it — contrast framing is a powerful technique, but this specific pattern ("not just X. You're Y.") was explicitly excluded. The skill needs a harder guard on this pattern.

---

## Recommended Skill Improvements (Next Iteration)

1. **Add hard rule:** Before finalizing output, scan for "not just [X]. [You're/It's/This is] [Y]." and rewrite.
2. **Add sentence budget enforcement:** Each copy block should include a max sentence count in the validation checklist, matched to format (ad copy: 4-8 sentences per section, emails: 5-10 total).
3. **Strengthen Orange trigger word list:** Add explicit "97 days," countdown-style urgency as an Orange-specific device (achievement-oriented buyers respond to time-bound strategic windows, not just scarcity).

---

## Files

```
iteration-1/
├── eval-0-forbidden-lexicon/
│   ├── without_skill/outputs/copy.md   ← 7/7 (100%)
│   └── with_skill/outputs/copy.md      ← 6/7 (86%)
├── eval-1-nlp-meta-programs/
│   ├── without_skill/outputs/copy.md   ← 7/9 (78%)
│   └── with_skill/outputs/copy.md      ← 9/9 (100%)
├── eval-2-framework-selection/
│   ├── without_skill/outputs/copy.md   ← 6/8 (75%)
│   └── with_skill/outputs/copy.md      ← 7/8 (88%)
└── eval-3-hoodieflags-worldcup/
    ├── without_skill/outputs/copy.md   ← 9/10 (90%)
    └── with_skill/outputs/copy.md      ← 10/10 (100%)
```
