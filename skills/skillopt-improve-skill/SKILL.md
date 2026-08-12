---
name: skillopt-improve-skill
description: Evaluate and improve an existing Agent Skill against a measurable goal using SkillOpt-style rollouts, reflection, bounded edits, and validation gates. Use when the user provides a skill, SKILL.md, or skill folder and asks to improve triggering, reliability, task success, tool use, evaluation behavior, or the skill artifact itself. Do not use to create an unrelated skill from scratch.
---

# SkillOpt Improve Skill

## Goal

Improve an existing skill toward a concrete objective using SkillOpt's discipline: treat the skill document as trainable state, run task rollouts, reflect on scored traces, propose bounded edits, apply only selected edits, and accept updates only when validation improves.

Do the work end to end when possible. If the user gives only a skill and a goal, create an evaluation plan, run a lightweight local optimization loop, and leave a clear improved artifact plus optimization notes. Ask only when the goal cannot be scored or the target skill cannot be found.

Resolve `<skill-dir>` to the directory containing this `SKILL.md` before running bundled scripts.

## Resource Map

- Read `references/skillopt-method.md` when deciding between the official SkillOpt package and a local Codex loop, or when configuring epochs, learning rate, slow update, meta skill, or validation gates.
- Read `references/eval-design.md` when the user did not provide eval cases or the improvement goal needs a measurable scoring rubric.
- Use `scripts/skillopt_workspace.py` to initialize an optimization workspace, apply SkillOpt-style JSON patches, summarize rollout results, and compare baseline vs candidate results.

## Workflow

1. Locate the target skill.
   - Prefer an explicit path from the user.
   - If the user names a skill, search `${CODEX_HOME:-$HOME/.codex}/skills`, `$HOME/.agents/skills`, and the current workspace.
   - Read the target `SKILL.md` and only the resources needed for the improvement goal.

2. Convert the goal into a measurable objective.
   - Define a primary score before editing: pass rate, rubric score, task success, validation errors fixed, reduced ambiguity, or user-provided metric.
   - Design 5-20 eval cases if none exist. Mark generated cases as synthetic and include both likely successes and likely failures.
   - Keep train/reflection cases separate from validation/gate cases when there are enough cases.

3. Choose the optimization mode.
   - Use official SkillOpt when there is a dataset, split, scoring function, and either a built-in benchmark or time to add an `EnvAdapter`. Follow `references/skillopt-method.md`.
   - Use the local Codex loop for ordinary Codex skill improvement, small eval sets, missing benchmark adapters, or when the fastest path is to forward-test with available tools/subagents.

4. Initialize an optimization workspace.
   - Run:

```bash
python3 "<skill-dir>/scripts/skillopt_workspace.py" init --skill /path/to/SKILL.md --goal "specific improvement goal" --out-dir /path/to/workspace
```

   - Add eval cases with `--case "id|prompt|criteria"` or `--cases-jsonl cases.jsonl` when available.
   - Store rollouts, patches, candidate skills, gate reports, rejected edits, and notes in that workspace.

5. Roll out the current skill.
   - Execute each eval task using the target skill as a real user would invoke it.
   - Capture prompt, output, pass/fail or score, failure reason, and any tool/test output.
   - Use independent subagents for forward-testing when available and appropriate; pass only the skill path and task request, not expected fixes.

6. Reflect and propose edits.
   - Analyze failures for common patterns, not one-off examples.
   - Also inspect successes for reusable strategies worth preserving.
   - Emit a SkillOpt-style JSON patch with at most the current edit budget:

```json
{
  "reasoning": "why these edits address recurring evidence",
  "edits": [
    {"op": "append", "content": "markdown to add"},
    {"op": "insert_after", "target": "exact existing text", "content": "markdown to insert"},
    {"op": "replace", "target": "exact existing text", "content": "replacement markdown"},
    {"op": "delete", "target": "exact existing text to remove"}
  ]
}
```

7. Aggregate, select, and update.
   - Deduplicate overlapping edits.
   - Rank by systematic impact, complementarity, generality, and actionability.
   - Keep the edit budget small: start with 2-4 edits for small skills, 4-8 for broad skills, then taper after each accepted iteration.
   - Apply the selected patch with:

```bash
python3 "<skill-dir>/scripts/skillopt_workspace.py" apply-patch --skill current.md --patch patch.json --out candidate.md --report patch_report.json
```

8. Gate the candidate.
   - Re-run validation cases against the candidate skill.
   - Accept only if the primary validation score strictly improves and there are no unacceptable regressions.
   - Reject patches that do not pass; save the reason so later reflections avoid repeating them.
   - Compare result files with:

```bash
python3 "<skill-dir>/scripts/skillopt_workspace.py" compare --baseline baseline_results.jsonl --candidate candidate_results.jsonl --report gate_report.json
```

9. Iterate like SkillOpt.
   - Repeat rollout, reflection, selection, update, and gate until the goal is achieved, the evaluation budget is exhausted, or repeated gates reject for the same reason.
   - At epoch boundaries, write a slow-update note: what improved, what regressed, what persistent failures remain.
   - Maintain a compact meta-skill note for the optimizer: lessons about how to improve this specific skill family.

10. Deliver the result.
   - Update the original skill only after a candidate passes the gate or the user explicitly asks for an ungated edit.
   - Report the final skill path, the goal, eval set size, baseline score, candidate score, accepted edits, rejected edits, and remaining risks.
   - Keep all generated optimization artifacts in the workspace unless the user asks to clean them up.

## Guardrails

- Do not optimize against a single anecdote unless the user explicitly wants a targeted edit.
- Do not add broad instructions that merely restate generic agent behavior.
- Do not leak validation answers into the skill.
- Do not edit unrelated skill resources unless the evidence points to them.
- Preserve skill frontmatter validity and run that skill's validation process after editing.
