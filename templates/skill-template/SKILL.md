---
name: your-skill-name
description: State what this skill does, the prompts or contexts that should trigger it, and any nearby tasks that should not trigger it.
---

# Skill Name

State the concrete outcome this skill must produce.

## Workflow

1. Identify the required inputs and inspect only the relevant context.
2. Perform the task in explicit, imperative steps.
3. Use bundled resources only when their stated condition applies.
4. Verify the result with the smallest check that can catch a meaningful failure.
5. Report the output, verification result, and any remaining limitation.

## Resources

- Read `references/example.md` only when the task needs detailed domain guidance.
- Run `scripts/example.py` only when deterministic processing is required.
- Reuse files from `assets/` when they are intended to appear in the output.

Delete this section and any unused resource directories when the skill needs no bundled resources.

## Constraints

- Preserve explicit user requirements and existing project conventions.
- Ask for input only when a missing choice would materially change the result.
- Keep optional detail out of `SKILL.md`; link directly to one-level-deep references.
- Do not add scripts for behavior that concise instructions can handle reliably.

## Verification

Define the checks that must pass before the skill can report completion.
