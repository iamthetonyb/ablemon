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
- **Demographics**: Age, location, income
- **Psychographics**: Interests, pain points
- **Tone Match**: Tone_Social (mask) vs Tone_Chronic (under pressure)
- **Macro-Classifications**: Core Need (Power/Achievement/Affiliation), Value System, Lifestyle Designation
- **Decision Strategy**: Direct (sensation driven) vs Derived (reason driven)
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
| **4U** | Urgent, Unique, Ultra-specific, Useful | Headlines |

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
