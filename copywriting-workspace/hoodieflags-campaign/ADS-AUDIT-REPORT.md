# ADS-AUDIT-REPORT

Date: 2026-03-07
Scope: HoodieFlags World Cup 2026 campaign strategy + current landing paths + TikTok creative export pipeline
Primary source doc: `/Users/abenton333/Desktop/ATLAS/copywriting-workspace/hoodieflags-campaign/HoodieFlags_WorldCup2026_Campaign.md`

## Executive Summary

### Overall status
- **Launch readiness:** Medium-High (core strategy, copy framework, naming conventions, and asset pipeline are in place)
- **Risk tier:** Medium (mainly budget/learning constraints and patent-claim compliance handling)
- **Most important constraint:** $1,000 total budget with high test complexity (12 variants + 2 platforms)

### What is strong
- Clear variant architecture (AIDA/PAS/FAB + meta-program coverage)
- Updated offer consistency in campaign doc: **$44.99**, **water-resistant**, **bulk discounts**
- Strong naming and UTM discipline framework
- TikTok 9:16 asset pipeline now implemented with traceable filenames and variant overlays

### What needs control immediately
1. Keep landing-page variable fixed in Phase 1 (single LP only)
2. Treat Phase 1 as **signal discovery**, not strict platform learning completion
3. Gate patent-heavy language with substantiation before scale
4. Keep optimization event pragmatic (ATC when purchase volume is insufficient)

---

## Hybrid Audit Findings (Strict + Pragmatic)

## 1) Budget/Test Feasibility

### Strict checklist view
- Meta budget adequacy (`M-ST1`) is below strict ideal when spread across multiple ad sets.
- TikTok budget sufficiency (`T12`) and learning completion (`T13`) are unlikely under low daily spend if evaluated by strict platform thresholds.

### Pragmatic sprint view
- For a micro-budget validation sprint, this is acceptable if the objective is explicit:
  - identify winning **creative angle + audience cluster + platform direction**
  - not full algorithmic maturity in every ad set/ad group
- Use staged optimization:
  - Phase 1: creative signal + ATC proxy when Purchase volume is thin
  - Phase 2: consolidate to winners and push toward Purchase optimization

### Decision
- **Proceed with sprint**, but do not interpret Phase 1 results as final CPA truth.

---

## 2) Tracking and UTM Integrity

### Current status
- Campaign doc has strong UTM conventions and naming discipline.
- Site telemetry artifacts indicate active analytics/pixel stack (Meta and TikTok tag artifacts present in page source).
- Live script runtime check on heavy Shopify collection timed out at default 30s in `analyze_landing.py`; tool and error handling work.

### Risks
- Data quality risk if campaign naming/UTM dimensions are not consistently appended to every ad variant and creative version.
- Variant-level video/frame-style tests need corresponding UTM metadata to avoid attribution blending.

### Required UTM expansion for TikTok tests
Add two parameters to all TikTok links:
- `utm_content`: variant ID (`V04`, `V09`, etc.)
- `utm_term`: frame style + duration (`crop_short22`, `padded_short22`, etc.)

---

## 3) TikTok Execution Constraints

### Confirmed implementation outputs
- 20 TikTok-ready MP4 files generated in `/Users/abenton333/Desktop/HF/tiktok_exports`
- All exports validated at **1080x1920**
- Short duration: **22.022s**
- Full duration: **34.176s**
- Decode check: **PASS** on all exports

### Creative test approach
- Correct for this budget: test frame style first, then variant winners.
- Avoid introducing landing-page split in the same window as creative framing test.

---

## 4) Offer and Message Consistency

### Verified alignment
- Campaign doc now reflects:
  - `$44.99`
  - `water-resistant`
  - `bulk discounts`
- Collection/product pages show $44.99 and pre-order language.
- Product source contains active volume-discount campaign metadata (buy more/save more structure).

### Remaining consistency check
- Ensure bulk discount callout is clearly visible in above-the-fold experience on mobile for all targeted PDP/collection variants.

---

## 5) Landing-Page Routing Recommendation

### Recommendation
- Phase 1: route all prospecting traffic to
  - `https://hoodieflags.com/collections/country-flags-2nd-gen`
- Phase 2+: allow controlled LP split test only after creative winner

### Rationale
- Keeps variable count manageable under $1,000
- Maintains message match with pre-order context
- Reduces dilution from broader `/collections` browsing paths

---

## 6) Patent Claim Integration (Controlled Add-On)

### Current policy for launch
- Keep core launch copy functional without patent claim dependency.
- Use patent-forward overlays selectively where the claim can be substantiated.

### Compliance note (required before scaling patent language)
- If running “patented technology / first ever / only” as explicit superiority claims, maintain internal proof packet (patent number, scope, and approved claim phrasing) for ad review and dispute handling.

---

## Launch Now vs Scale Later

### Launch Now
- Keep LP fixed in Phase 1
- Run frame-style + variant matrix as defined in `TIKTOK-TEST-MATRIX.md`
- Maintain strict UTM/video naming hygiene
- Use ATC proxy where purchase volume is too low for stable signal

### Scale Later
- Expand patent-forward messaging once compliance packet is finalized
- Introduce country-specific PDP routing after creative winner
- Increase budget concentration on top variant/platform only

