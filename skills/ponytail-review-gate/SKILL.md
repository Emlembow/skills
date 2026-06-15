---
name: ponytail-review-gate
description: Add or operate a Ponytail adversarial review gate in a repo. Use when a user wants Ponytail minimalism, over-engineering review, or a reusable completion gate for code changes.
---

# Ponytail Review Gate

Use this skill when installing, explaining, or applying the Ponytail review gate to a project.

## What It Provides

- Ponytail mode: prefer YAGNI, standard library, native platform features, installed dependencies, one-line fixes, then minimum custom code.
- Ponytail review: diff-only review for unnecessary complexity.
- Ponytail audit: optional whole-repo over-engineering audit, only when explicitly requested.
- Ponytail adversarial review gate: mandatory completion gate for code-writing tasks.

## Gate Text

Add this to a target repo's `AGENTS.md`:

```md
For any task that writes, edits, refactors, scaffolds, or reviews code in this repo, use the `ponytail-adversarial-review` skill from the `ponytail-review-gate` plugin.

The completion gate is mandatory: run Ponytail adversarial review on the changed code, decide corrective actions, apply accepted fixes, and verify before finishing.
```

## Completion Workflow

1. Keep Ponytail's ladder in mind before editing.
2. After editing, collect the changed surface with `git status --short` and relevant diffs.
3. Run `ponytail-review` against changed code only.
4. Accept, reject, or defer each finding.
5. Apply accepted fixes.
6. Verify with the smallest relevant check.

Do not run `ponytail-audit` for ordinary per-diff completion. Reserve it for explicit whole-repo audit requests.
