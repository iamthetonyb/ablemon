---
name: remotion-video
description: "Remotion video production best practices. Use when creating programmatic videos with Remotion, React video compositions, or animated content. Triggers on: remotion, video, animation, composition, render video, video template, motion graphics."
---

# Remotion Best Practices

> Programmatic video creation with React + Remotion.

## When to Use
- Creating video content programmatically
- Building video templates for marketing/social
- Animated presentations or explainers

## Captions
- Use `@remotion/captions` for subtitle generation
- Prefer `.srt` format for cross-platform compatibility
- Position captions at bottom 15% of frame

## Using FFmpeg
- Remotion bundles FFmpeg — use `@remotion/renderer` APIs
- For custom processing: `npx remotion ffmpeg ...`
- Avoid direct FFmpeg calls; use Remotion's abstractions

## Audio Visualization
- Use `@remotion/media-utils` for audio analysis
- `getAudioDuration()` for timing
- `visualizeAudio()` for waveform/spectrum data

## Sound Effects
- Keep audio files in `public/audio/`
- Use `<Audio>` component with `startFrom` for precise timing
- Layer multiple `<Audio>` components for mixing

## Key Patterns

```tsx
// Composition structure
import { Composition } from 'remotion';

export const RemotionRoot: React.FC = () => {
    return (
        <Composition
            id="MyVideo"
            component={MyVideo}
            durationInFrames={30 * 20}  // 20 seconds at 30fps
            fps={30}
            width={1920}
            height={1080}
        />
    );
};
```

### Animation Best Practices
- Use `useCurrentFrame()` and `interpolate()` for animations
- Spring animations: `spring({ fps, frame, config: { damping: 200 } })`
- Sequence components: `<Sequence from={30}>` for timing
- Keep compositions pure — no side effects

### Rendering
```bash
npx remotion render src/index.ts MyVideo output/video.mp4
```
