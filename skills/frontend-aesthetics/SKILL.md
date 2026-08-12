---
name: frontend-aesthetics
description: Design or revise distinctive, polished web interfaces with intentional typography, color, composition, motion, and responsive behavior. Use for visual work on HTML, CSS, React, landing pages, dashboards, prototypes, or browser-based product UI. Preserve an explicit reference design or established design system when supplied; do not use for non-visual backend work.
---

# Frontend Aesthetics

Produce an interface that feels specific to its product, audience, and content rather than assembled from generic defaults.

## Workflow

1. Inspect the existing product before choosing a direction.
   - Identify the framework, component library, tokens, layout conventions, and reusable assets.
   - Preserve working behavior and established visual language unless the user asks for a redesign.
   - Treat screenshots, mockups, brand guides, and explicit user preferences as higher priority than this skill.

2. State one design direction internally before coding.
   - Name the mood, visual reference, type strategy, palette, and defining compositional move.
   - Choose one strong idea that suits the product; avoid stacking unrelated effects.
   - Read [references/direction-library.md](references/direction-library.md) only when the brief lacks a clear direction or you need implementation patterns.

3. Establish a small visual system.
   - Define reusable color, type, spacing, radius, border, shadow, and motion tokens.
   - Use one dominant surface treatment and one restrained accent strategy.
   - Select typography for the content and audience. Do not default to the same fashionable font pairing across unrelated projects.

4. Compose around the content.
   - Build hierarchy with scale, weight, alignment, density, contrast, and whitespace.
   - Use asymmetry, overlap, or broken-grid composition only when it improves the story or task.
   - Avoid interchangeable card grids, centered hero templates, and decoration that could belong to any product.

5. Add motion and detail selectively.
   - Use motion to explain state changes, guide attention, or establish rhythm.
   - Prefer one coordinated entrance or transition system over many unrelated animations.
   - Respect `prefers-reduced-motion`; keep interactions usable without animation.

6. Verify the implementation at realistic sizes.
   - Check narrow mobile, wide desktop, long content, empty states, and keyboard focus.
   - Confirm readable contrast, semantic structure, sensible tap targets, and visible focus treatment.
   - Remove any effect that harms legibility, performance, or task completion.

## Constraints

- Preserve the project's framework and installed dependencies unless a change is necessary.
- Prefer CSS and platform capabilities before adding animation or styling libraries.
- Do not add external font or image requests when the environment cannot load them.
- Avoid emoji as interface icons when an icon set or simple SVG is available.
- Do not sacrifice accessibility, responsiveness, or product clarity for novelty.
- Do not force a dramatic redesign onto a maintenance task that asks for a narrow visual fix.

## Quality gate

Before finishing, answer yes to all of these:

- Does the visual direction fit this specific product and audience?
- Is the hierarchy obvious within a few seconds?
- Does the interface remain coherent at mobile and desktop widths?
- Are interaction states, keyboard focus, and reduced motion handled?
- Can any decorative layer be removed without losing the core direction? If yes, remove it.

Implement the result and report only the design decisions that help the user review the change.
