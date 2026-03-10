---
name: ui-ux-pro-max
description: "Design system generation and UI/UX best practices. Use this skill when building any user interface, web app, mobile app, dashboard, or frontend component. Triggers on: design, UI, UX, frontend, component, layout, responsive, dark mode, accessibility, design system, colors, typography, animation."
---

# UI/UX Pro Max — Design Intelligence

> Professional design systems with accessibility, responsive patterns, and modern aesthetics.

## When to Apply

- **Must Use**: New UI projects, design system creation, component libraries
- **Recommended**: Adding new pages/views, redesigning existing UI
- **Skip**: Backend-only changes, CLI tools, API-only services

## Step 1: Analyze Requirements

Before writing any code:
- What platform? (web, mobile, desktop)
- What framework? (React, Next.js, vanilla HTML/CSS)
- Light mode, dark mode, or both?
- Accessibility requirements? (WCAG 2.2 AA minimum)
- Brand colors/fonts if any?

## Step 2: Generate Design System (REQUIRED)

Every UI project needs a design system. Generate these tokens:

```css
:root {
    /* Colors — use HSL for easy theming */
    --color-primary: hsl(220, 80%, 55%);
    --color-primary-hover: hsl(220, 80%, 45%);
    --color-secondary: hsl(280, 65%, 55%);
    --color-surface: hsl(220, 15%, 98%);
    --color-surface-elevated: hsl(0, 0%, 100%);
    --color-text: hsl(220, 15%, 12%);
    --color-text-secondary: hsl(220, 10%, 45%);
    --color-border: hsl(220, 15%, 88%);
    --color-success: hsl(145, 65%, 42%);
    --color-warning: hsl(38, 92%, 50%);
    --color-error: hsl(0, 72%, 51%);

    /* Typography */
    --font-sans: 'Inter', system-ui, -apple-system, sans-serif;
    --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
    --text-xs: 0.75rem;
    --text-sm: 0.875rem;
    --text-base: 1rem;
    --text-lg: 1.125rem;
    --text-xl: 1.25rem;
    --text-2xl: 1.5rem;
    --text-3xl: 1.875rem;

    /* Spacing (4px base) */
    --space-1: 0.25rem;
    --space-2: 0.5rem;
    --space-3: 0.75rem;
    --space-4: 1rem;
    --space-6: 1.5rem;
    --space-8: 2rem;
    --space-12: 3rem;

    /* Shadows */
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
    --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.1);
    --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.1);

    /* Borders */
    --radius-sm: 0.375rem;
    --radius-md: 0.5rem;
    --radius-lg: 0.75rem;
    --radius-full: 9999px;

    /* Transitions */
    --transition-fast: 150ms ease;
    --transition-base: 200ms ease;
    --transition-slow: 300ms ease;
}

/* Dark mode */
@media (prefers-color-scheme: dark) {
    :root {
        --color-surface: hsl(220, 15%, 8%);
        --color-surface-elevated: hsl(220, 15%, 12%);
        --color-text: hsl(220, 15%, 92%);
        --color-text-secondary: hsl(220, 10%, 60%);
        --color-border: hsl(220, 15%, 22%);
    }
}
```

## Quick Reference

### 1. Accessibility (CRITICAL)
- Color contrast: 4.5:1 minimum for text, 3:1 for large text
- Focus indicators: visible on all interactive elements
- Screen reader: semantic HTML, ARIA labels, alt text
- Keyboard: all interactions keyboard-navigable
- Motion: respect `prefers-reduced-motion`

### 2. Touch & Interaction (CRITICAL)
- Minimum touch target: 44×44px
- Comfortable touch target: 48×48px
- Spacing between targets: 8px minimum
- Hover states: always pair with focus states

### 3. Performance (HIGH)
- First Contentful Paint < 1.8s
- Largest Contentful Paint < 2.5s
- Cumulative Layout Shift < 0.1
- Lazy load images below fold
- Use `loading="lazy"` on images

### 4. Layout & Responsive (HIGH)
- Mobile-first: start at 320px
- Breakpoints: 640px (sm), 768px (md), 1024px (lg), 1280px (xl)
- Max content width: 1200px with auto margins
- Use CSS Grid for layouts, Flexbox for components

### 5. Typography (MEDIUM)
- Line height: 1.5 for body, 1.2 for headings
- Max line length: 65-75 characters
- Font loading: `font-display: swap`

### 6. Animation (MEDIUM)
- Duration: 150-300ms for UI, 300-500ms for page transitions
- Easing: `ease-out` for entrances, `ease-in` for exits
- Transform-only: avoid animating layout properties
- Respect `prefers-reduced-motion: reduce`

### 7. Forms & Feedback (MEDIUM)
- Labels above inputs (not placeholder-only)
- Real-time validation on blur
- Clear error messages next to field
- Loading states on submit buttons

## Pre-Delivery Checklist

- [ ] **Visual**: Colors consistent, no orphaned styles
- [ ] **Interaction**: All buttons/links have hover + focus + active states
- [ ] **Light/Dark**: Both modes tested (if applicable)
- [ ] **Layout**: Tested at 320px, 768px, 1024px, 1440px
- [ ] **Accessibility**: Color contrast passes, keyboard navigation works
- [ ] **Performance**: No layout shifts, images optimized
