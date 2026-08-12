---
name: ponytail-audit
description: Audit an entire repository for avoidable complexity and rank what to delete, simplify, or replace with standard-library or native behavior. Use only when the user explicitly asks for a whole-repository over-engineering, bloat, deletion, or Ponytail audit. Produce a one-shot report and do not apply fixes.
---

Scan the whole tree instead of a diff. Rank findings by the largest safe cut first.

## Tags

Same as ponytail-review:

- `delete:` dead code, unused flexibility, speculative feature. Replacement: nothing.
- `stdlib:` hand-rolled thing the standard library ships. Name the function.
- `native:` dependency or code doing what the platform already does. Name the feature.
- `yagni:` abstraction with one implementation, config nobody sets, layer with one caller.
- `shrink:` same logic, fewer lines. Show the shorter form.

## Hunt

Deps the stdlib or platform already ships, single-implementation interfaces,
factories with one product, wrappers that only delegate, files exporting one
thing, dead flags and config, hand-rolled stdlib.

## Output

One line per finding, ranked: `<tag> <what to cut>. <replacement>. [path]`.
End with `net: -<N> lines, -<M> deps possible.` Nothing to cut: `Lean already. Ship.`

## Boundaries

Complexity only, correctness bugs, security holes, and performance go to a
normal review pass. Lists findings, applies nothing. One-shot.
Treat this as a one-shot audit unless the user asks to continue.
