Create Remotion videos using the ATLAS Remotion skill.

Follow the remotion-video SKILL.md:
- Use @remotion/captions for subtitles (.srt format)
- Audio files in public/audio/
- Compositions: 30fps, 1920×1080 default
- Animations: useCurrentFrame(), interpolate(), spring()
- Sequences: <Sequence from={frame}> for timing
- Render: npx remotion render src/index.ts {CompositionId} output/video.mp4

Reference: atlas/skills/library/remotion-video/SKILL.md
