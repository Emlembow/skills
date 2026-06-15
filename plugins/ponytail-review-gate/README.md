# Ponytail Review Gate

Portable Codex plugin that bundles:

- `ponytail`: lazy senior developer mode.
- `ponytail-review`: diff-only over-engineering review.
- `ponytail-audit`: optional whole-repo over-engineering audit.
- `ponytail-adversarial-review`: mandatory completion gate for code-writing tasks.

## Install

Clone or install `Emlembow/skills`, then add the Codex marketplace at:

```text
.agents/plugins/marketplace.json
```

Install the plugin named:

```text
ponytail-review-gate
```

## Add The Gate To A Repo

Add this to that repo's `AGENTS.md`:

```md
For any task that writes, edits, refactors, scaffolds, or reviews code in this repo, use the `ponytail-adversarial-review` skill from the `ponytail-review-gate` plugin.

The completion gate is mandatory: run Ponytail adversarial review on the changed code, decide corrective actions, apply accepted fixes, and verify before finishing.
```

## Attribution

Based on upstream Ponytail by Dietrich Gebert:

```text
https://github.com/DietrichGebert/ponytail
```

This package keeps the upstream Ponytail skills together with Emlembow's project review gate wrapper.
