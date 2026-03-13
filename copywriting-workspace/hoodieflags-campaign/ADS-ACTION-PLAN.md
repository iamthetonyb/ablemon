# ADS-ACTION-PLAN

Date: 2026-03-09
Goal: Run a balanced learn+buy sprint that preserves the 4 Meta ad sets and 2 TikTok ad groups, but removes the spend dilution that was blocking both learning and purchases.

## Phase 0 — Pre-Launch

1. Finalize tracking and destination control
- Keep all Phase 1 prospecting traffic on `/collections/country-flags-2nd-gen`.
- Append variant and asset metadata to all UTMs.

2. Load only the launch ad count
- Meta Phase 1 live ads: `V01`, `V12`, `V02`, `V10`, `V03`, `V11`, `V04`, `V05`
- Meta reserve only: `V06`, `V07`, `V08`, `V09`
- TikTok Phase 1 live ads: `V04`, `V09`, `V05`, `V03`
- TikTok reserve only: `V06`, `V11`

3. Lock platform controls
- Meta Phase 1 uses ABO, not CBO.
- TikTok launch uses crop short22 exports first.
- Wi-Fi-only stays OFF.
- No dayparting in Phase 1.

## Phase 1 — Controlled Launch

### Meta (Days 1-5)
- 1 ABO prospecting campaign
- 4 ad sets at `$12/day` each
- 8 live Meta ads total
- Total Meta Phase 1 spend: `$240`

### TikTok (Days 1-2)
- 1 conversion campaign
- 2 ad groups at `$20/day` each
- 4 live TikTok ads total
- Total TikTok Phase 1 spend: `$80`

### Guardrails
- Do not introduce LP tests.
- Do not add new ad sets or ad groups.
- Do not promote reserve variants yet.

### Early pause candidates
- Meta: `>= $60` spent, `0` ATC, weak CTR, weak outbound click efficiency
- TikTok: `>= $20` spent, `0` ATC, weak CTR, weak hook quality

## Phase 2 — Winner Optimization

### Meta (Days 6-14)
- Move the top 2 ad sets into a new winner CBO campaign
- Set total campaign budget to `$40/day` for 9 days
- Keep the best ad from each surviving set live
- Add 1 challenger per surviving set

### Meta challenger rule
- If Set B survives and country-specific heritage creative is ready, use it
- If Set B survives and country-specific creative is not ready, use `V09` with the best generic heritage visual
- Other surviving sets use the strongest aligned reserve variant

### TikTok (Days 3-7)
- Pause the losing ad group
- Keep the winning ad group only
- Set budget to `$22/day` for 5 days
- Keep the best ad live and add the reserve variant only if the live winner weakens

### Ranking order
- Meta: `Purchase/ATC`, then `IC rate`, then `unique CTR`, then `outbound CPC`
- TikTok: `Purchase/ATC`, then `watch quality`, then `CTR`, then `CPC`

## Phase 3 — Meta Scale + Retarget

### Meta only (Days 15-21)
- 1 winning prospecting ad set at `$24/day`
- 1 retargeting ad set at `$6/day`
- Total Meta Phase 3 spend: `$210`

### Retargeting setup
- Audience: 30-day website visitors and recent ATC / IC users, excluding purchasers
- Ad: best-performing Phase 2 winner with urgency added to the opening line
- Budgeting: ABO

## Budget Totals

- Meta: `$240 + $360 + $210 = $810`
- TikTok: `$80 + $110 = $190`
- Total: `$1,000`
