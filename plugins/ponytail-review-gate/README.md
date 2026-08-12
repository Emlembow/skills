# Ponytail Review Gate

Portable Codex and Claude Code plugin that bundles:

- `ponytail`: lazy senior developer mode.
- `ponytail-review`: diff-only over-engineering review.
- `ponytail-audit`: optional whole-repo over-engineering audit.
- `ponytail-adversarial-review`: mandatory completion gate for code-writing tasks.

## Install in Codex

```bash
codex plugin marketplace add Emlembow/skills
codex plugin add ponytail-review-gate@emlembow-skills
```

Start a new task after installation so Codex loads the bundled skills.

## Install in Claude Code

```bash
claude plugin marketplace add Emlembow/skills
claude plugin install ponytail-review-gate@emlembow-skills
```

## Add the gate to a repository

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
