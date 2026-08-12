---
name: ponytail-review-gate
description: Add or run a lean-code completion gate that challenges unnecessary complexity before code work is declared complete. Use when a user asks for Ponytail minimalism, an over-engineering review, a reusable AGENTS.md gate, or a final simplicity pass on changed code. Do not substitute it for correctness or security review.
---

# Ponytail Review Gate

Install, explain, or apply the Ponytail review gate to a project. This portable skill must remain useful even when the full plugin is not installed.

## What It Provides

- Ponytail mode: prefer YAGNI, standard library, native platform features, installed dependencies, one-line fixes, then minimum custom code.
- Ponytail review: diff-only review for unnecessary complexity.
- Ponytail audit: optional whole-repo over-engineering audit, only when explicitly requested.
- Ponytail adversarial review gate: mandatory completion gate for code-writing tasks.

## Choose the available surface

- If `ponytail-adversarial-review` is available, use it for the full bundled workflow.
- Otherwise, apply the completion workflow in this skill directly.
- If the user asks to install the full plugin, use the repository's Codex or Claude marketplace instructions. Do not imply that a portable `npx skills` install includes plugin-specific behavior.

## Gate text

Add this to a target repo's `AGENTS.md`:

```md
For any task that writes, edits, refactors, or scaffolds code in this repo, run a Ponytail simplicity review on the changed code before finishing. Use `ponytail-adversarial-review` when available; otherwise use `ponytail-review-gate`.

The completion gate is mandatory: identify avoidable complexity, decide each corrective action, apply accepted fixes, and verify before finishing. Keep correctness, security, accessibility, and explicitly requested behavior intact.
```

## Completion workflow

1. Prefer deletion, standard-library behavior, native platform features, installed dependencies, one-line fixes, then minimum custom code.
2. Collect the changed surface with `git status --short`, relevant diffs, and new files.
3. Hunt only avoidable complexity: dead code, speculative abstractions, new dependencies, hand-rolled standard behavior, over-wide configuration, and repeated boilerplate.
4. Mark each finding `accept`, `reject`, or `defer`. Reject a simplification that would harm correctness, security, accessibility, data-loss prevention, necessary framework convention, or explicit requirements.
5. Apply every accepted fix and repeat the review if the fix changes code.
6. Run the smallest relevant test, lint, typecheck, or smoke check.
7. Report `Lean already. Ship.` when nothing should change; otherwise summarize accepted, rejected, and deferred actions.

Do not run `ponytail-audit` for ordinary per-diff completion. Reserve it for explicit whole-repo audit requests.
