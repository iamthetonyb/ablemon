# TIKTOK-EXPORT-VALIDATION

Date: 2026-03-07
Export directory: `/Users/abenton333/Desktop/HF/tiktok_exports`

## Summary
- Total MP4 exports: **20**
- Resolution target: **1080x1920**
- Duration targets: **34.176s (full)** and **22.022s (short)**
- Decode/open check: **PASS for all files**

## Specs (from ffprobe)

| File | Width | Height | Duration (s) |
|---|---:|---:|---:|
| HF_TT_MASTER_captioned_crop_full.mp4 | 1080 | 1920 | 34.176 |
| HF_TT_MASTER_captioned_crop_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_MASTER_captioned_padded_full.mp4 | 1080 | 1920 | 34.176 |
| HF_TT_MASTER_captioned_padded_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_MASTER_clean_crop_full.mp4 | 1080 | 1920 | 34.176 |
| HF_TT_MASTER_clean_crop_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_MASTER_clean_padded_full.mp4 | 1080 | 1920 | 34.176 |
| HF_TT_MASTER_clean_padded_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_V03_captioned_crop_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_V03_captioned_padded_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_V04_captioned_crop_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_V04_captioned_padded_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_V05_captioned_crop_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_V05_captioned_padded_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_V06_captioned_crop_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_V06_captioned_padded_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_V09_captioned_crop_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_V09_captioned_padded_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_V11_captioned_crop_short22.mp4 | 1080 | 1920 | 22.022 |
| HF_TT_V11_captioned_padded_short22.mp4 | 1080 | 1920 | 22.022 |

## Decode Validation
- Command used: `ffmpeg -v error -i <file> -t 1 -f null -`
- Result: PASS for all 20 files

