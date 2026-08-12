# Direction Library

Read this reference only when the brief does not already provide a visual direction or when a chosen direction needs concrete implementation patterns. Select one direction and adapt it to the product; do not combine the entire catalog.

## Direction prompts

### Editorial

- Pair a characterful serif or display face with a quiet sans-serif body face.
- Use strong scale changes, measured line lengths, captions, rules, and deliberate whitespace.
- Favor warm paper-like neutrals, ink tones, and one saturated accent.
- Let content order and typography create the composition before adding containers.

### Technical or operational

- Use dense but legible information hierarchy, tabular numerals, and precise alignment.
- Build depth with borders, tonal surfaces, and state color rather than large shadows.
- Reserve high-chroma color for status, selection, or action.
- Make scanning, comparison, and keyboard operation more important than spectacle.

### Expressive product launch

- Choose one recognizable motif: oversized type, a diagonal rhythm, cropped imagery, a strong texture, or a single geometric system.
- Coordinate the hero, section transitions, and calls to action around that motif.
- Use a limited palette with a clear dominant color and a sharp accent.
- Keep later sections calmer so the launch moment remains distinctive.

### Minimal premium

- Use excellent typography, precise spacing, restrained color, and material detail.
- Prefer subtle borders, optical alignment, and carefully tuned hover or focus states.
- Avoid treating emptiness alone as luxury; hierarchy and craft must carry the design.

### Playful or cultural

- Derive shape, color, illustration, and motion from a concrete reference appropriate to the audience.
- Vary rhythm and scale while keeping controls predictable.
- Use humor in details, not in accessibility labels or critical state messages.

## Implementation patterns

### Fluid type and spacing

Use bounded fluid values rather than many arbitrary breakpoints:

```css
:root {
  --step--1: clamp(0.82rem, 0.78rem + 0.18vw, 0.94rem);
  --step-0: clamp(1rem, 0.94rem + 0.28vw, 1.18rem);
  --step-1: clamp(1.3rem, 1.12rem + 0.8vw, 1.85rem);
  --step-2: clamp(2rem, 1.45rem + 2.2vw, 3.4rem);
  --space: clamp(1rem, 0.7rem + 1.4vw, 2rem);
}
```

### Layered atmosphere

Keep layers few and tied to the direction:

```css
.surface {
  background:
    radial-gradient(circle at 15% 10%, color-mix(in srgb, var(--accent) 18%, transparent), transparent 35rem),
    linear-gradient(145deg, var(--surface), var(--surface-raised));
}
```

### Coordinated motion

Define a shared duration and easing curve, then disable nonessential movement when requested:

```css
:root {
  --motion-fast: 160ms;
  --motion-enter: 420ms;
  --ease-out: cubic-bezier(.2, .8, .2, 1);
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    scroll-behavior: auto !important;
    animation-duration: 1ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 1ms !important;
  }
}
```

## Common failure modes

- Reusing the same purple-blue gradient, rounded card grid, and centered hero regardless of context.
- Choosing a display font without checking loading, language coverage, or body readability.
- Adding texture, glow, blur, and motion simultaneously without a dominant idea.
- Hiding weak hierarchy behind decorative backgrounds.
- Making every section visually loud, leaving no rhythm or emphasis.
- Treating mobile as a scaled-down desktop instead of recomposing it.
- Ignoring empty, loading, error, focus, hover, and reduced-motion states.
