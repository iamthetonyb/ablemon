# TIKTOK-TEST-MATRIX

Date: 2026-03-09
Video source: `/Users/abenton333/Desktop/HF/Hoodie Flags Commercial V3 - Clean.mp4`
Export directory: `/Users/abenton333/Desktop/HF/tiktok_exports`

## Launch Policy

- Launch with crop short22 exports only
- Keep 2 live ads per ad group in the first pass
- Hold padded versions as fallback only
- Hold `V06` and `V11` as reserve variants only

## Naming Convention

- Launch assets: `HF_TT_VXX_captioned_crop_short22.mp4`
- Reserve fallback assets remain available in the same export folder

## Export Validation Summary

- All files: **1080x1920**
- Full duration: **34.176s**
- Short duration: **22.022s**
- Decode check: **PASS** on all files

## Phase 1 — Days 1-2

| Ad Group | Live Ads | Budget | Landing Page | UTM Content | UTM Term | Decision Rule |
|---|---|---:|---|---|---|---|
| G1 High Energy | `HF_TT_V04_captioned_crop_short22.mp4`, `HF_TT_V09_captioned_crop_short22.mp4` | `$20/day` | `/collections/country-flags-2nd-gen` | `V04`, `V09` | `crop_short22` | Rank by `Purchase/ATC`, then watch quality, then CTR, then CPC |
| G2 Value & Community | `HF_TT_V05_captioned_crop_short22.mp4`, `HF_TT_V03_captioned_crop_short22.mp4` | `$20/day` | `/collections/country-flags-2nd-gen` | `V05`, `V03` | `crop_short22` | Rank by `Purchase/ATC`, then watch quality, then CTR, then CPC |

## Phase 2 — Days 3-7

| Outcome | Action | Budget |
|---|---|---:|
| One clear winning ad group | Pause the losing group and keep the winner only | `$22/day` for 5 days |
| Both groups weak but one is still clearly less bad | Keep the higher-ranked group and add the reserve variant for that group | `$22/day` for 5 days |
| Both groups catastrophically weak | Pause TikTok only if both groups show `0` ATC and clearly broken CTR / hook quality after the full Day 1-2 screen | Hold remaining TikTok budget |

## Reserve Rules

- If Group 1 wins but the live ads weaken, add `HF_TT_V06_captioned_crop_short22.mp4`
- If Group 2 wins but the live ads weaken, add `HF_TT_V11_captioned_crop_short22.mp4`
- Only move to padded versions if crop assets visibly clip key content or materially underperform

## Guardrails

1. Do not split landing pages in Phase 1.
2. Do not turn Wi-Fi-only on.
3. Do not add a third TikTok ad group.
4. Pause ad-level losers after meaningful spend instead of keeping all 6 variants live.

## Generated Asset Inventory Used in This Plan

- `HF_TT_V04_captioned_crop_short22.mp4`
- `HF_TT_V09_captioned_crop_short22.mp4`
- `HF_TT_V05_captioned_crop_short22.mp4`
- `HF_TT_V03_captioned_crop_short22.mp4`
- `HF_TT_V06_captioned_crop_short22.mp4`
- `HF_TT_V11_captioned_crop_short22.mp4`

