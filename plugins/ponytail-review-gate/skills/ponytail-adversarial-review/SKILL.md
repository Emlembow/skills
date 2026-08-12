---
name: ponytail-adversarial-review
description: Enforce an adversarial simplicity review before completing code changes, including a decision on every corrective action, accepted fixes, and verification. Use when repository instructions require a Ponytail completion gate or the user asks to challenge a diff for unnecessary complexity before finishing. Do not replace correctness or security review.
---

# Ponytail Adversarial Review

Use this skill as a completion gate for code changes in a target repo. The main agent may write code normally, but it cannot finish until Ponytail has challenged the diff and the main agent has either fixed or explicitly declined each finding.

## Source Skill

Use the vendored Ponytail skills in this plugin:

- Ponytail mode: `../ponytail/SKILL.md`
- Ponytail review: `../ponytail-review/SKILL.md`
- Ponytail audit, optional whole-repo audit only, not the normal completion gate: `../ponytail-audit/SKILL.md`

If those paths are missing, fall back to installed `$ponytail` and `$ponytail-review` skills if available. If neither is available, perform the same review manually using Ponytail's ladder: YAGNI, standard library, native platform features, installed dependency, one line, then minimum code.

## Workflow

1. Before editing, keep Ponytail's ladder in mind: delete, stdlib, native, installed dependency, one line, minimum custom code. Do not simplify away security, trust-boundary validation, data-loss prevention, accessibility, explicitly requested behavior, explicitly requested explanations/reports, hardware calibration knobs, or the smallest runnable check for non-trivial logic.

2. After writing code, collect the changed surface:
   - Run `git status --short`.
   - Run `git diff -- <changed tracked files>`.
   - For new untracked code files, read enough of the file to review the introduced code.

3. Run the adversarial review:
   - Apply `ponytail-review` to the changed code.
   - Hunt only unnecessary complexity: dead code, speculative abstractions, new dependencies, hand-rolled stdlib/native behavior, over-wide config, repeated boilerplate, or code that can become smaller without losing behavior.
   - Do not flag the smallest useful runnable check or a real-world calibration knob as bloat.
   - Do not run `ponytail-audit` for ordinary per-diff completion; reserve it for explicit whole-repo audit requests.
   - Use Ponytail's terse finding format internally: `file:L<line>: <tag>: <what>. <replacement>.`

4. Main-agent corrective discussion:
   - For each Ponytail finding, decide one of: `accept`, `reject`, or `defer`.
   - Accept when the replacement preserves the user's requested behavior and improves simplicity.
   - Reject only when the finding would remove explicit requirements, correctness, security, accessibility, error handling that prevents data loss, or necessary framework convention.
   - Defer only when the change is real but outside the user's requested scope; name the follow-up plainly.

5. Update code before completion:
   - Apply every accepted corrective action.
   - If corrective changes modify code, repeat the review on the new diff until there are no accepted findings left.
   - Do not finish while an accepted finding remains unpatched.

6. Verify:
   - Run the smallest relevant checks for the touched area, such as `npm run lint`, unit tests, typecheck, or an Expo health check.
   - For non-trivial logic, leave or run one check that would fail if the logic broke.
   - If a check cannot run, say why and keep the Ponytail review result visible in the final summary.

## Final Response

Keep the final concise. Include:

- What changed.
- Verification result.
- A short Ponytail note: `Lean already. Ship.` or the accepted/rejected corrective actions.

Do not paste a long review transcript unless the user asks for it.
