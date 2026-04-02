---
name: copy-editing
description: "Advanced Seven Sweeps editing protocol + Deep NLP Tone Calibration for copy. Use this to refine, edit, polish, or rewrite existing copy to maximize conversion and ensure NLP tone alignment (Tone_Social vs Tone_Chronic). Triggers on: edit, polish, refine, rewrite, proofread, conversion optimize."
---

# Copy-Editing Protocol (Seven Sweeps + Deep NLP Tone Calibration)

> **Advanced Copy Editing Protocol**
> Use this skill when asked to edit, refine, or rewrite existing copy. Do NOT just fix grammar. You must eliminate algorithmic AI signatures, align the copy with the target's NLP tone profile, and run the coreyhaines31 Seven Sweeps framework.

## Triggers

- Command: "edit", "polish", "refine", "rewrite", "proofread", "conversion optimize", "sweep", "tone check"

## Inputs

| Name | Type | Required | Description |
|------|------|----------|-------------|
| copy | string | Yes | The existing text/copy to edit |
| tone_social | string | No | The target's superficial/mask tone (Scale 1-12) |
| tone_chronic | string | No | The target's pressure/default tone (Scale 1-12) |
| objective | string | No | The ultimate conversion goal of the text |

## Outputs

| Name | Type | Description |
|------|------|-------------|
| edited_copy | string | The final, polished copy |
| sweeps_applied | list | Which sweeps resulted in major changes |
| tone_calibration | string | How the copy was adjusted for Tone_Social/Tone_Chronic |

---

## PART 1: DEEP NLP TONE CALIBRATION

Before editing, evaluate if the existing copy matches the psychological profile of the target audience.

### The Tone Hierarchy Scale
1. Failure / Useless / Apathy
2. Hopeless / Making Amends / Grief
3. Propitiation / Sympathy
4. Numb / Terror / Despair / Fear
5. Anxiety / Covert hostility
6. Unexpressed resentment / No sympathy
7. Resentment / Hate / Anger
8. Pain / Hostility / Antagonism
9. Monotony / Boredom
10. Disinterested / Contentment
11. Mild interest / Conservatism
12. Strong interest / Cheerful / Enthusiasm

**Calibration Rules:**
1. **Tone_Social (The Mask)**: The headline and opening hook MUST match the target's social mask. (e.g., If they project "Enthusiasm" (12), open with high energy and bold claims).
2. **Tone_Chronic (The Reality)**: The problem agitation and the call-to-action MUST speak to their chronic tone. (e.g., If their chronic tone is "Anxiety" (5), the CTA must reduce risk and offer safety/guarantees).
3. **Decision Strategy**: Does the target buy based on *Direct Sensation* (It looks/feels right) or *Derived Logic* (Here are 5 reasons why)? Edit the adjectives and proof points to match.

---

## PART 2: FORBIDDEN LEXICON PURGE (Pre-Sweep)

Scan for and aggressively remove these AI fingerprints before doing structural edits:

**Abstract Nouns:** tapestry, realm, landscape, testament, multitude, plethora, arena, cornerstone, paradigm, synergy
**Actionless Verbs:** delve, utilize, leverage, foster, align, augment, underscore, navigate, unlock, unleash
**Padding Adjectives:** robust, crucial, essential, dynamic, transformative, seamless, paramount, cutting-edge
**Structural Fingerprints:** Contrastive negation ("It's not just about X. It's about Y."), Forced triads ("Focused. Aligned. Measurable.")

---

## PART 3: THE SEVEN SWEEPS (coreyhaines31)

Run these 7 editing sweeps in exact order.

### Sweep 1: Clarity (The 12-Year-Old Test)
- Readability first. Can a 12-year-old understand exactly what is being offered?
- Rule: One idea per sentence.
- Rule: Break up paragraphs longer than 3 lines.

### Sweep 2: Voice and Tone (The Human Test)
- Read the copy aloud. Does a real human speak this way?
- Fix stiff transitions ("furthermore", "moreover").
- Replace passive voice ("The platform is used by...") with active voice ("10,000 teams use the platform...").

### Sweep 3: "So What?" (The Outcome Test)
- Locate every feature or claim in the copy.
- Ask "So what?" and append the actual benefit to the user.
- *Bad:* "We offer 24/7 analytics tracking."
- *Good:* "Track your analytics 24/7, so you wake up knowing exactly which ads made money while you slept."

### Sweep 4: Prove It (The Evidence Test)
- Eradicate empty superlatives ("We are the highest quality provider").
- Replace with concrete proof, data, or credentials ("Used by Shopify, Stripe, and 4,000+ others").
- If proof doesn't exist in the prompt, leave a `[Insert Proof Here]` placeholder.

### Sweep 5: Specificity (The Detail Test)
- Vague copy doesn't convert.
- Replace "many" with "4,200+".
- Replace "fast" with "in 14 days or less".
- Replace "save time" with "get 5 hours back every week".

### Sweep 6: Heightened Emotion (The Contrast Test)
- Ensure there is stark contrast between the "Before" (Pain) and "After" (Solution).
- Are you amplifying the Tone_Chronic pain point effectively?
- Hook the reader in the first 30 characters.

### Sweep 7: Zero Risk (The Friction Test)
- Is the Call-To-Action (CTA) frictionless?
- Is the exact next step clear? ("Click here to schedule" instead of "Get in touch")
- Have you reversed the risk? (Money-back guarantee, free trial, no credit card required, cancel anytime).

---

## Examples

**Input Copy:** "Leverage our robust platform to seamlessly integrate your marketing data. It's not just about saving time, it's about transforming your business paradigm. Click here to learn more."
**Target NLP:** Tone_Social: Conservatism (11). Tone_Chronic: Anxiety (5).

**Output Copy:**
```
Stop manually moving your marketing data between spreadsheets. 

Our system connects to your ad accounts in 2 clicks, pulling your exact ROAS into one dashboard. You get 5 hours back every week, without worrying about human mapping errors.

Try it free for 14 days (no credit card required).
```
**Sweeps Applied:** Cleared forbidden lexicon (leverage, robust, seamless, paradigm). Specificity added (2 clicks, ROAS, 5 hours). Zero Risk added (no credit card).
**NLP Calibration:** Tone_Chronic (Anxiety) addressed by "worrying about human mapping errors" and "no credit card required."
