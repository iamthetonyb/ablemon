---
name: paid-ads
description: "Paid advertising campaign creation and optimization. Use for Google Ads, Meta Ads, LinkedIn Ads, TikTok Ads — campaign structure, ad copy, audience targeting, bid strategies, and optimization. Triggers on: ads, advertising, campaign, Google Ads, Meta Ads, Facebook Ads, LinkedIn Ads, PPC, paid media, ad copy, retargeting, ROAS."
---

# Paid Ads

> Campaign structure, ad copy, targeting, and optimization across all major platforms.

## Before Starting

### 1. Campaign Goals
- Primary objective: awareness, traffic, leads, sales?
- Target CPA/ROAS?
- Monthly budget range?
- Timeline: launch date, duration?

### 2. Product & Offer
- What's being promoted?
- What makes it different?
- Current pricing/offer/promotion?
- Landing page URL?

### 3. Audience (Deep NLP Profiling)
Ad copy conversion strictly depends on matching the target's psychological profile. Ensure you define:
- **Demographics**: Age, location, income
- **Psychographics**: Interests, core pain points
- **Tone_Social (The Mask)**: The superficial tone they project (Scale 1-12). Write the **Hook / Headline** in this tone. Example: "Enthusiasm (12)" requires high energy.
- **Tone_Chronic (The Reality)**: The baseline tone under pressure (Scale 1-12). Write the **Pain Agitation / CTA** in this tone. Example: "Anxiety (5)" requires reversing risk and offering safety.
- **Macro-Classifications**: Core Need (Power/Achievement/Affiliation), Value System (e.g., Orange Achiever, Green Humanistic), Lifestyle Designation.
- **Decision Strategy**: 
  - *Direct*: They buy based on sensation ("Looks right, feels right"). Use kinesthetic/visual adjectives.
  - *Derived (The WHY)*: They buy based on logic. Give a linear list of REASONS.
- **Lookalike**: Existing customer profiles mapping to NLP semantic filters

### 4. Current State
- Running ads currently?
- Previous performance data?
- Competitor ads to reference?

## Campaign Structure

### Account Organization
```
Account
├── Campaign: [Goal] - [Product] - [Audience]
│   ├── Ad Set/Group: [Targeting Segment]
│   │   ├── Ad: [Creative Variant A]
│   │   ├── Ad: [Creative Variant B]
│   │   └── Ad: [Creative Variant C]
│   └── Ad Set/Group: [Targeting Segment 2]
└── Campaign: [Retargeting]
```

### Naming Convention
`{Platform}_{Goal}_{Audience}_{Creative}_{Date}`
Example: `META_CONV_LOOKALIKE_VIDEO-A_2026Q1`

### Budget Allocation
- 70% to proven performers
- 20% to testing new audiences
- 10% to testing new creatives

## Ad Copy Frameworks

| Formula | Structure | Best For |
|---------|-----------|----------|
| **AIDA** | Attention → Interest → Desire → Action | Awareness campaigns |
| **PAS** | Problem → Agitate → Solution | Pain-driven products |
| **BAB** | Before → After → Bridge | Transformation offers |
| **FAB** | Feature → Advantage → Benefit | Technical products, SaaS, informed buyers |
| **4U** | Urgent, Unique, Ultra-specific, Useful | Headlines & short-form hooks |

### Framework Selection Guide

Choose based on NLP profiling:
- **AIDA**: Best for Toward-motivated audiences (levels 10-12 Tone_Social). Build desire progressively.
- **PAS**: Best for Away-From audiences (levels 4-7 Tone_Chronic). Agitate the pain they already feel.
- **BAB**: Best for transformation/lifestyle products where the audience can visualize a better state. Works strongest with Visual and Kinesthetic rep systems.
- **FAB**: Best for Derived (WHY) decision strategy buyers who need logical reasons in a linear list. Lead with spec data, show the functional advantage, close with the emotional payoff.
- **4U**: Best for TikTok/Reels hooks (0-3 seconds) and Google search headlines where every character counts. Apply as a checklist to any headline before publishing.

### FAB Protocol (Feature → Advantage → Benefit)

Use this when the audience evaluates specs before buying. Orange (Achiever) and Yellow (Systems Thinker) Spiral profiles respond strongest.

1. **Feature**: State the specific, measurable capability with empirical data.
   - Include exact specs, quantities, or technical attributes
   - Example: "Water-resistant fabric, full-coverage flag design, 40+ countries, $44.99/ea."

2. **Advantage**: Explain the functional improvement over the status quo or competitors.
   - Use comparison anchors ("vs. stadium merch at $80-120")
   - External frame: reference industry standard or competitor weakness
   - Example: "Delivered before kickoff. No stadium line. No inflated event pricing."

3. **Benefit**: Translate the advantage into an emotional or experiential outcome.
   - Match to the audience's rep system (Visual: "see yourself", Auditory: "hear yourself saying", Kinesthetic: "feel the quality")
   - Example: "You show up looking sharper than anyone who grabbed whatever was left at the gate."

**When NOT to use FAB**: Red (Impulsive) profiles and high-urgency contexts — these buyers don't want a spec sheet, they want a demand. Use AIDA or PAS instead.

### BAB Protocol (Before → After → Bridge)

Use this for transformation offers where the gap between current state and desired state is vivid and emotional.

1. **Before**: Paint the current frustrating situation in the audience's own words.
   - Use present tense to make it feel immediate
   - Example: "You're scrambling through search results looking for fan gear that actually represents your country."

2. **After**: Show the desired outcome as if they already achieved it.
   - Use future visualization or "picture this" language
   - Example: "You walk into opening day, colors on, and your people spot you across the stadium in 30 seconds."

3. **Bridge**: Position your product as the single connector between Before and After.
   - Keep it tight — one sentence connecting the problem to the solution
   - Example: "HoodieFlags gets you there. 40+ countries. Delivered before kickoff."

### 4U Headline Checklist

Apply to every headline before publishing. Score 1-4:

1. **Urgent**: Is there a time constraint? ("Before June 11", "Limited stock")
2. **Unique**: Is it differentiated from every other ad they scroll past? ("The first & only")
3. **Ultra-specific**: Does it use precise numbers or details? ("42 countries", "$44.99/ea.")
4. **Useful**: Does it promise a clear benefit or outcome? ("Half the stadium price")

If any headline scores below 3/4, rewrite before publishing.

### Platform Character Limits

| Platform | Headline | Description | Primary Text |
|----------|----------|-------------|--------------|
| Google Search | 30 chars × 15 | 90 chars × 4 | — |
| Meta/IG | 40 chars | 155 chars | 125 chars |
| LinkedIn | 70 chars | — | 150 chars |

## Creative Testing Hierarchy
1. **Message** (what you say) — test first
2. **Format** (image vs video vs carousel) — test second
3. **Hook** (first 3 seconds of video) — test third
4. **Visual style** (colors, layout) — test last

## Optimization Levers

| Metric | Action if Poor |
|--------|---------------|
| Low CTR (<1%) | Change creative/headline |
| High CPC | Broaden targeting or improve quality score |
| Low conversion rate | Fix landing page, check audience match |
| High CPA | Pause low performers, scale winners |

## Retargeting Strategies

### Audience Tiers
1. **Hot** (0-7 days): Cart abandoners, pricing viewers → direct CTA
2. **Warm** (7-30 days): Page visitors, engagers → social proof + CTA
3. **Cool** (30-90 days): Past visitors → education + reintroduction

### Required Exclusions
- Current customers (unless upsell)
- Converters from last 7 days
- Internal team members

## Common Mistakes
- Skipping the learning phase (need ~50 conversions)
- Too many ad sets with thin budgets
- Not excluding converters from prospecting
- Testing too many variables at once
- Ignoring mobile experience on landing page
